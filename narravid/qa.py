"""Post-render QA: sync check, sampled frames, scene final-state frames, contact sheets."""
from __future__ import annotations

import shutil

from . import tools


def sync_check(project, cfg: dict) -> dict:
    fps = int(cfg["render"]["fps"])
    tol = float(cfg["qa"]["sync_tolerance_s"])
    timing = project.read_json("timing.json")
    final = project.p("render/final.mp4")
    audio = project.p("audio/final.wav")
    vdur = _video_duration(final)
    adur = tools.ffprobe_duration(audio)
    frame = 1.0 / fps
    late = [{"scene": s["id"], "scene_start": s["start"], "sentence_start": s["sentence_start"],
             "delta": round(s["start"] - s["sentence_start"], 3)}
            for s in timing["scenes"] if abs(s["start"] - s["sentence_start"]) > tol]
    return {"video_duration": round(vdur, 4), "audio_duration": round(adur, 4),
            "duration_ok": abs(vdur - adur) <= frame + 1e-6, "frame": round(frame, 4),
            "scenes_checked": len(timing["scenes"]), "scenes_out_of_sync": late,
            "max_scene_delta": round(max([abs(s["start"] - s["sentence_start"]) for s in timing["scenes"]] or [0]), 3),
            "ok": abs(vdur - adur) <= frame + 1e-6 and not late}


def _video_duration(mp4) -> float:
    for s in tools.ffprobe_streams(mp4):
        if s.get("codec_type") == "video" and s.get("duration"):
            return float(s["duration"])
    return tools.ffprobe_duration(mp4)


def frames_and_sheets(project, cfg: dict, log=print) -> dict:
    ff = tools.find("ffmpeg")
    final = project.p("render/final.mp4")
    out = project.p("qa/frames")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    dur = tools.ffprobe_duration(final)
    step = float(cfg["qa"]["frame_interval_s"])
    timing = project.read_json("timing.json")
    shots = [("t", round(k * step, 2)) for k in range(int(dur // step) + 1)]
    shots += [(s["id"], round(max(s["start"], s["end"] - 0.6), 2)) for s in timing["scenes"]]
    shots.sort(key=lambda x: x[1])
    index = []
    for n, (label, t) in enumerate(shots):
        f = out / f"f{n:05d}.jpg"
        tools.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", final, "-frames:v", "1",
                   "-vf", "scale=480:-2", "-q:v", "4", f])
        index.append({"file": f.name, "t": t, "kind": "sample" if label == "t" else "scene_end",
                      "scene": None if label == "t" else label})
    sheets = project.p("qa/sheets")
    if sheets.exists():
        shutil.rmtree(sheets)
    sheets.mkdir(parents=True)
    tools.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", out / "f%05d.jpg", "-vf", "tile=5x5:padding=6:color=0x0B1020",
               sheets / "sheet_%03d.jpg"])
    log(f"QA: {len(index)} frames, {len(list(sheets.glob('*.jpg')))} contact sheet(s)")
    project.write_json("qa/frames.json", index)
    return {"frames": len(index), "sheets": sorted(p.name for p in sheets.glob("*.jpg"))}
