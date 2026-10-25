"""Stage 7 — render chunks with HyperFrames, assemble, mux, burn captions, compress."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

from . import captions, tools


def caption_font_dir() -> Path:
    """libass needs TTF/OTF; convert the bundled Inter woff2 once (fontTools)."""
    d = tools.renderer_dir() / "fonts"
    ttf = d / "Inter-Regular.ttf"
    if not ttf.exists():
        from fontTools.ttLib import TTFont
        d.mkdir(parents=True, exist_ok=True)
        src = tools.renderer_dir() / "node_modules" / "@fontsource" / "inter" / "files" / "inter-latin-500-normal.woff2"
        f = TTFont(str(src))
        f.flavor = None
        f.save(str(ttf))
    return d


def write_captions(project, cfg: dict) -> list[dict]:
    words = project.read_json("words.json")
    cc = cfg["captions"]
    cues = captions.segment(words, int(cc["max_chars"]), float(cc["min_duration"]))
    project.p("captions.srt").write_text(captions.to_srt(cues), encoding="utf-8")
    project.p("captions.ass").write_text(captions.to_ass(cues, size=int(cc["font_size"])), encoding="utf-8")
    return cues


def _hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def render_chunks(project, cfg: dict, log=print, only: list[str] | None = None, draft: bool = False) -> list[Path]:
    timing = project.read_json("timing.json")
    rc = cfg["render"]
    out = project.p("render/chunks")
    out.mkdir(parents=True, exist_ok=True)
    manifest = project.read_json("render/chunks/manifest.json", {})
    env = tools.node_env()
    files = []
    for ch in timing["chunks"]:
        cdir = project.p(f"composition/{ch['id']}")
        mp4 = out / f"{ch['id']}{'_draft' if draft else ''}.mp4"
        key = _hash(cdir / "index.html") + ("d" if draft else rc["quality"])
        if only and ch["id"] not in only:
            files.append(mp4)
            continue
        if mp4.exists() and manifest.get(mp4.name) == key:
            log(f"Render: {ch['id']} up to date, skipping")
            files.append(mp4)
            continue
        log(f"Render: {ch['id']} ({ch['frames']} frames)")
        def attempt(workers):
            cmd = tools.hyperframes_cmd() + ["render", str(cdir), "-o", str(mp4), "-f", str(rc["fps"]),
                                             "-q", "draft" if draft else rc["quality"], "-w", str(workers), "--quiet"]
            return tools.run_streaming(cmd, cwd=tools.renderer_dir(), env=env, log=log)
        code = attempt(rc["workers"])
        if code != 0 or not mp4.exists():       # headless Chrome can crash under load: retry once, gently
            log(f"Render: {ch['id']} failed (exit {code}); retrying with one worker")
            mp4.unlink(missing_ok=True)
            code = attempt(1)
        if code != 0 or not mp4.exists():
            raise RuntimeError(f"HyperFrames render failed for {ch['id']} (exit {code}). See the log above; "
                               "try `npx hyperframes doctor` in renderer/.")
        n = _frame_count(mp4)
        if n != ch["frames"]:
            raise RuntimeError(f"{ch['id']}: rendered {n} frames, expected {ch['frames']} — sync would drift")
        manifest[mp4.name] = key
        project.write_json("render/chunks/manifest.json", manifest)
        files.append(mp4)
    return files


def _frame_count(mp4: Path) -> int:
    p = tools.run([tools.find("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
                   "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(mp4)])
    return int(p.stdout.strip().split(",")[0])


def assemble(project, cfg: dict, chunks: list[Path], log=print, burn: bool | None = None) -> dict:
    rc = cfg["render"]
    burn = rc["burn_captions"] if burn is None else burn
    ff = tools.find("ffmpeg")
    rdir = project.p("render")
    lst = rdir / "chunks.txt"
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in chunks), encoding="utf-8")
    silent = rdir / "video_silent.mp4"
    tools.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
               "-map", "0:v", "-c", "copy", silent], log=log)
    audio = project.p("audio/final.wav")
    nocap = rdir / "final_nocaptions.mp4"
    tools.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", silent, "-i", audio, "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac", "-b:a", rc["audio_bitrate"], "-movflags", "+faststart", nocap], log=log)
    final = rdir / "final.mp4"
    if burn:
        fonts = caption_font_dir()
        shutil.copy2(project.p("captions.ass"), rdir / "captions.ass")
        rel_fonts = Path(shutil.copytree(fonts, rdir / "fonts", dirs_exist_ok=True)).name
        log("Burning captions (re-encode, libx264 CRF 17)")
        tools.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", silent.name, "-i", str(audio),
                   "-vf", f"ass=captions.ass:fontsdir={rel_fonts}", "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(rc["fps"]),
                   "-c:a", "aac", "-b:a", rc["audio_bitrate"], "-movflags", "+faststart", final.name],
                  cwd=rdir, log=log)
    else:
        shutil.copy2(nocap, final)
    shutil.copy2(project.p("captions.srt"), rdir / "final.srt")
    silent.unlink(missing_ok=True)
    return {"final": str(final), "nocaptions": str(nocap), "srt": str(rdir / "final.srt")}


def compress(project, cfg: dict, log=print) -> list[str]:
    """Two-pass x264 copy under the target size; split into equal parts if the bitrate would be too low."""
    rc = cfg["render"]
    src = project.p("render/final.mp4")
    dur = tools.ffprobe_duration(src)
    target_bits = float(rc["compressed_target_mb"]) * 8 * 1024 * 1024 * 0.96   # 4% container headroom
    a_kbps = int(rc["compressed_audio_kbps"])
    min_v = int(rc["compressed_min_video_kbps"])
    parts = 1
    while (target_bits / (dur / parts)) / 1000 - a_kbps < min_v and parts < 16:
        parts += 1
    seg = dur / parts
    v_kbps = int((target_bits / seg) / 1000 - a_kbps)
    outdir = project.p("render/compressed")
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True)
    if src.stat().st_size * 8 <= target_bits:
        # already under the target: re-encoding would only lose quality (or grow the file)
        dst = outdir / "final_small.mp4"
        shutil.copy2(src, dst)
        log(f"Compress: final.mp4 is already {src.stat().st_size / 1048576:.1f} MB (≤ target); copied as is")
        return [str(dst)]
    src_v = next((int(s["bit_rate"]) // 1000 for s in tools.ffprobe_streams(src)
                  if s.get("codec_type") == "video" and str(s.get("bit_rate", "")).isdigit()), None)
    if src_v:
        v_kbps = min(v_kbps, src_v)        # never exceed what the content actually needs
    ff = tools.find("ffmpeg")
    outs = []
    log(f"Compress: {parts} file(s), {v_kbps} kbps video + {a_kbps} kbps audio each")
    for k in range(parts):
        name = outdir / (f"final_part{k + 1}of{parts}.mp4" if parts > 1 else "final_small.mp4")
        rng = ["-ss", f"{k * seg:.3f}", "-t", f"{seg:.3f}"] if parts > 1 else []
        passlog = str(outdir / f"x264_{k}")
        common = [ff, "-y", "-hide_banner", "-loglevel", "error", *rng, "-i", str(src), "-c:v", "libx264",
                  "-preset", "slow", "-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.5)}k",
                  "-bufsize", f"{v_kbps * 3}k", "-pix_fmt", "yuv420p", "-passlogfile", passlog]
        tools.run(common + ["-pass", "1", "-an", "-f", "mp4", "NUL"], log=log)
        tools.run(common + ["-pass", "2", "-c:a", "aac", "-b:a", f"{a_kbps}k", "-movflags", "+faststart", str(name)], log=log)
        size_mb = name.stat().st_size / 1024 / 1024
        log(f"  {name.name}: {size_mb:.1f} MB")
        outs.append(str(name))
    for f in outdir.glob("x264_*"):
        f.unlink(missing_ok=True)
    return outs


def layout_qa(project, cfg: dict, log=print) -> dict:
    from . import design
    timing = project.read_json("timing.json")
    checks = project.read_json("checks.json", {"scenes": {}})
    chrome = tools.run(tools.hyperframes_cmd() + ["browser", "path"], cwd=tools.renderer_dir(),
                       env=tools.node_env()).stdout.strip().splitlines()[-1].strip()
    chunks = []
    for ch in timing["chunks"]:
        scs = []
        for s in timing["scenes"]:
            if s["chunk"] != ch["id"]:
                continue
            local_end = s["end"] - ch["start"]
            # final state: just before the scene starts fading out
            t = max(s["local_start"] + 0.05, local_end - design.MOTION["scene_out"] - 0.1)
            scs.append({"id": s["id"], "t": round(t, 3),
                        "schematic": checks["scenes"].get(s["id"], {}).get("schematic", False)})
        chunks.append({"dir": ch["id"], "comp_id": ch["id"], "scenes": scs})
    root = str(project.p("composition").resolve())
    payload = {"root": root, "chrome": chrome, "out_dir": str(project.p("qa/scenes").resolve()),
               "safe": design.SAFE, "caption_top": design.CAPTION_TOP, "min_px": design.TYPE["min_px"],
               "chunks": chunks}
    res = tools.node_script("layout_qa.mjs", payload, timeout=1800)
    n_err = sum(len(v["errors"]) for v in res["scenes"].values())
    res["ok"] = n_err == 0
    res["error_count"] = n_err
    project.write_json("qa/layout.json", res)
    log(f"Layout QA: {len(res['scenes'])} scenes, {n_err} problem(s)")
    for sid, v in res["scenes"].items():
        for e in v["errors"][:4]:
            log(f"  {sid}: {e['kind']} — {e.get('text', '')}")
    return res
