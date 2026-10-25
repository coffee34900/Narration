"""Stage orchestration + command line.

    python -m narravid.pipeline new  <name> --script S.txt --audio A.wav
    python -m narravid.pipeline run  <name> [--stage align] [--no-llm] [--auto-ignore]
    python -m narravid.pipeline models
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from . import align as align_mod
from . import audio, checks, compose, config, qa, render, storyboard, verify
from .project import STAGES, Project
from .sentences import parse_script


class StageError(RuntimeError):
    """An expected, actionable failure (shown to the user without a traceback)."""


def new_project(name: str, script: Path, audio_file: Path, root: Path | None = None) -> Project:
    pr = Project(name, root=root).create()
    for old in pr.p("input").glob("*"):
        old.unlink()
    shutil.copy2(script, pr.p("input") / ("script" + (script.suffix or ".txt")))
    shutil.copy2(audio_file, pr.p("input") / ("audio" + audio_file.suffix.lower()))
    return pr


# ---------------------------------------------------------------------- stages
def stage_ingest(pr: Project, cfg: dict, log) -> dict:
    s, a = pr.input_script(), pr.input_audio()
    if not s or not a:
        raise StageError("Project needs a script (.txt) and an audio file. Add them on the New project tab.")
    text = s.read_text(encoding="utf-8-sig", errors="replace")
    parsed = parse_script(text)
    if not parsed["parts"]:
        raise StageError("No sentences found in the script.")
    pr.write_json("script_original.json", parsed)
    n_s = sum(len(p["sentences"]) for p in parsed["parts"])
    log(f"Script: {len(parsed['parts'])} parts, {n_s} sentences "
        f"({', '.join(str(p['part']) for p in parsed['parts'])})")
    audio.to_analysis_wav(a, pr.p("work/analysis.wav"), log)
    audio.to_master_wav(a, pr.p("audio/original.wav"), log)
    from . import tools
    dur = tools.ffprobe_duration(pr.p("work/analysis.wav"))
    log(f"Audio: {dur / 60:.1f} min; analysis copy 16 kHz mono; original kept for the final mix")
    return {"parts": len(parsed["parts"]), "sentences": n_s, "duration": dur}


def asr_key(wav: Path, cfg: dict) -> str:
    """Cache key for a transcript: the audio's content + the recogniser used."""
    import hashlib
    h = hashlib.sha1()
    with open(wav, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return f"{h.hexdigest()}:{cfg['asr']['engine']}:{cfg['asr']['model']}"


def stage_align(pr: Project, cfg: dict, log) -> dict:
    from . import asr, tools
    wav = pr.p("work/analysis.wav")
    key = asr_key(wav, cfg)
    cached = pr.read_json("work/asr.json")
    if cached and cached.get("key") == key:
        words = cached["words"]
        log(f"ASR: using cached transcript ({len(words)} words)")
    else:
        words = asr.transcribe(wav, cfg, log)
        pr.write_json("work/asr.json", {"key": key, "words": words})
    sil = audio.detect_silences(wav, cfg["silence"]["noise_db"], cfg["silence"]["min_duration"])
    pr.write_json("work/silences.json", sil)
    dur = tools.ffprobe_duration(wav)
    script = pr.read_json("script_original.json")
    res = align_mod.align(script, words, dur)
    pr.write_json("align/original_words.json", res["words"])
    pr.write_json("align/original_sentences.json", res["sentences"])
    pr.write_json("align/data.json", res["data"])
    pr.write_json("align/report.json", res["report"])
    issues = verify.detect(res, words, sil, cfg)
    # keep earlier decisions for the same findings
    old = {(i["type"], round(i["start"], 1)): i for i in pr.read_json("issues.json", {"issues": []})["issues"]}
    for i in issues:
        o = old.get((i["type"], round(i["start"], 1)))
        if o:
            i["decision"] = o.get("decision")
            if o.get("manual_cut"):
                i["manual_cut"] = o["manual_cut"]
    pr.write_json("issues.json", {"issues": issues})
    rep = res["report"]
    log(f"Alignment: {rep['words_matched_pct']}% of script words matched "
        f"({rep['script_matched_pct']}% tokens; {rep['asr_matched_pct']}% of recognised tokens)")
    if rep["checks"]:
        log(f"Alignment sanity problems: {rep['checks'][:5]}")
    if rep["words_matched_pct"] < cfg["alignment"]["match_pct_warning"]:
        log("WARNING: low match rate — is this the right audio for this script? Check the ASR model/language.")
    by_type = {t: sum(1 for i in issues if i["type"] == t) for t in verify.CHOICES}
    log(f"Verification: {by_type['insertion']} insertion(s), {by_type['repetition']} repetition(s), "
        f"{by_type['omission']} omission(s) — review them in the Verification panel")
    return {"match_pct": rep["words_matched_pct"], "issues": by_type}


def stage_verify(pr: Project, cfg: dict, log, auto_ignore: bool = False) -> dict:
    from . import tools
    issues = pr.read_json("issues.json", {"issues": []})["issues"]
    if auto_ignore:
        for i in issues:
            i["decision"] = i.get("decision") or "ignore"
        pr.write_json("issues.json", {"issues": issues})
    try:
        pl = verify.plan(issues)
    except ValueError as e:
        raise StageError(str(e)) from e
    original = pr.read_json("script_original.json")
    final_text = verify.edit_script(original, pl["inserts"], pl["removes"])
    pr.p("script_final.txt").write_text(final_text, encoding="utf-8")
    script = parse_script(final_text)
    pr.write_json("script.json", script)
    cuts = audio.merge_cuts(pl["cuts"])
    pr.write_json("audio/cuts.json", cuts)
    audio.apply_cuts(pr.p("audio/original.wav"), pr.p("audio/final.wav"), cuts, log)
    if cuts:
        log(f"Cut {len(cuts)} region(s), {sum(e - s for s, e in cuts):.2f}s total, all on silence: {cuts}")
    words = pr.read_json("work/asr.json")["words"]
    words = align_mod.remap_asr(words, cuts)
    dur = tools.ffprobe_duration(pr.p("audio/final.wav"))
    res = align_mod.align(script, words, dur)
    pr.write_json("words.json", res["words"])
    pr.write_json("sentences.json", res["sentences"])
    # residual check on the final audio/script
    sil = [(s2, e2) for s, e in pr.read_json("work/silences.json", [])
           for s2, e2 in [(audio.remap_time(s, cuts), audio.remap_time(e, cuts))] if s2 is not None and e2 is not None]
    residual = verify.detect(res, words, sil, cfg)
    ignored = sum(1 for i in issues if i["decision"] == "ignore")
    res["report"]["residual_issues"] = [{k: i[k] for k in ("type", "start", "end", "detail")} for i in residual]
    pr.write_json("align/final_report.json", res["report"])
    log(f"Final alignment: {res['report']['words_matched_pct']}% words matched; "
        f"{len(residual)} residual finding(s) ({ignored} issue(s) were marked ignore)")
    if res["report"]["checks"]:
        raise StageError(f"Final timing failed sanity checks: {res['report']['checks'][:5]}")
    return {"cuts": len(cuts), "inserted": len(pl["inserts"]), "removed": len(pl["removes"]),
            "residual": len(residual)}


def stage_storyboard(pr: Project, cfg: dict, log, use_llm: bool = True, parts: list[int] | None = None) -> dict:
    script = pr.read_json("script.json")
    if not script:
        raise StageError("Run Verification first (it produces the final script).")
    existing = pr.read_json("storyboard.json") if parts else None
    sb = storyboard.build(script, cfg, use_llm=use_llm, log=log, parts=parts, existing=existing)
    pr.write_json("storyboard.json", sb)
    log(f"Storyboard: {len(sb['scenes'])} scenes")
    return {"scenes": len(sb["scenes"])}


def stage_checks(pr: Project, cfg: dict, log, use_llm: bool | None = None) -> dict:
    script, sb = pr.read_json("script.json"), pr.read_json("storyboard.json")
    if not sb:
        raise StageError("No storyboard yet.")
    use = cfg["llm"]["self_check"] if use_llm is None else use_llm
    res = checks.run(script, sb, cfg, use_llm=use, log=log)
    pr.write_json("checks.json", res)
    log(f"Checks: {len(res['flagged'])} flagged scene(s); coverage "
        f"{'ok' if not res['coverage'] else res['coverage']}")
    return {"flagged": len(res["flagged"]), "ok": res["ok"]}


def stage_compose(pr: Project, cfg: dict, log) -> dict:
    ck = pr.read_json("checks.json")
    if not ck or pr.stage_status("checks") == "stale":
        stage_checks(pr, cfg, log, use_llm=False)
        ck = pr.read_json("checks.json")
    timing = compose.build(pr, cfg, log)
    render.write_captions(pr, cfg)
    lq = render.layout_qa(pr, cfg, log)
    return {"chunks": len(timing["chunks"]), "layout_problems": lq["error_count"]}


def gate_render(pr: Project, override_layout: bool = False) -> None:
    ck = pr.read_json("checks.json")
    if not ck:
        raise StageError("Run the accuracy checks first.")
    if pr.stage_status("checks") == "stale":
        raise StageError("Storyboard changed since the last check — re-run Checks.")
    if ck["flagged"]:
        raise StageError(f"{len(ck['flagged'])} scene(s) are flagged and not approved: {ck['flagged'][:8]}. "
                         "Edit or approve them in the Storyboard editor.")
    if [c for c in ck["coverage"] if c["level"] == "error"]:
        raise StageError(f"Coverage problems: {ck['coverage']}")
    lq = pr.read_json("qa/layout.json")
    if not lq:
        raise StageError("Run Compose first (it runs the layout QA).")
    if not lq["ok"] and not override_layout:
        bad = [k for k, v in lq["scenes"].items() if v["errors"]]
        raise StageError(f"Layout QA found {lq['error_count']} problem(s) in scenes {bad[:8]}. Fix the text "
                         "(shorter items / fewer items / another template) and re-run Compose.")


def stage_render(pr: Project, cfg: dict, log, override_layout: bool = False, compressed: bool = True) -> dict:
    gate_render(pr, override_layout)
    if pr.stage_status("compose") in ("stale", "pending"):
        raise StageError("Composition is out of date — re-run Compose.")
    chunks = render.render_chunks(pr, cfg, log)
    out = render.assemble(pr, cfg, chunks, log)
    if compressed:
        out["compressed"] = render.compress(pr, cfg, log)
    log(f"Render: {out['final']}")
    return out


def stage_qa(pr: Project, cfg: dict, log) -> dict:
    sync = qa.sync_check(pr, cfg)
    fr = qa.frames_and_sheets(pr, cfg, log)
    lq = pr.read_json("qa/layout.json", {})
    ck = pr.read_json("checks.json", {})
    report = {"sync": sync, "frames": fr, "layout_ok": lq.get("ok"), "layout_problems": lq.get("error_count"),
              "flagged_scenes": ck.get("flagged"), "coverage": ck.get("coverage"),
              "approved_scenes": [k for k, v in (ck.get("scenes") or {}).items() if v["status"] == "approved"],
              "alignment": pr.read_json("align/final_report.json")}
    report["ok"] = bool(sync["ok"] and lq.get("ok") and not ck.get("flagged"))
    pr.write_json("qa/report.json", report)
    log(f"QA: duration video {sync['video_duration']}s vs audio {sync['audio_duration']}s "
        f"({'ok' if sync['duration_ok'] else 'MISMATCH'}); max scene offset {sync['max_scene_delta']}s; "
        f"{len(sync['scenes_out_of_sync'])} scene(s) beyond tolerance")
    return report


STAGE_FUNCS = {"ingest": stage_ingest, "align": stage_align, "verify": stage_verify,
               "storyboard": stage_storyboard, "checks": stage_checks, "compose": stage_compose,
               "render": stage_render, "qa": stage_qa}


def run_stage(pr: Project, stage: str, log=None, **kw) -> dict:
    cfg = config.load()
    log = log or pr.log
    pr.set_stage(stage, "running")
    log(f"=== {stage} ===")
    try:
        out = STAGE_FUNCS[stage](pr, cfg, log, **kw)
    except StageError as e:
        pr.set_stage(stage, "error", error=str(e))
        log(f"ERROR in {stage}: {e}")
        raise
    except Exception as e:  # noqa: BLE001
        pr.set_stage(stage, "error", error=f"{type(e).__name__}: {e}")
        log(f"ERROR in {stage}: {type(e).__name__}: {e}\n{traceback.format_exc()[-2000:]}")
        raise
    pr.set_stage(stage, "done", result=out)
    return out


def run_all(pr: Project, log=None, from_stage: str | None = None, **opts) -> None:
    """Run every stage that is not up to date (resumable)."""
    started = from_stage is None
    for st in STAGES:
        if not started and st == from_stage:
            started = True
        if not started:
            continue
        if pr.stage_status(st) == "done" and from_stage != st:
            continue
        kw = {k: v for k, v in opts.items() if k in STAGE_KW.get(st, ())}
        run_stage(pr, st, log, **kw)


STAGE_KW = {"verify": ("auto_ignore",), "storyboard": ("use_llm",), "checks": ("use_llm",),
            "render": ("override_layout", "compressed")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="narravid")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--script", required=True, type=Path)
    n.add_argument("--audio", required=True, type=Path)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--stage", choices=STAGES)
    r.add_argument("--from", dest="from_stage", choices=STAGES)
    r.add_argument("--no-llm", action="store_true", help="heuristic storyboard, no LLM self-check")
    r.add_argument("--auto-ignore", action="store_true", help="mark undecided verification issues as ignore")
    r.add_argument("--no-compressed", action="store_true")
    sub.add_parser("models")
    a = ap.parse_args(argv)
    if a.cmd == "models":
        from . import models_detect
        print(models_detect.format_table(models_detect.detect_all(config.load()["llm"]["base_url"])))
        return 0
    if a.cmd == "new":
        pr = new_project(a.name, a.script, a.audio)
        print(f"Created {pr.dir}")
        return 0
    pr = Project(a.name)
    if not pr.exists():
        print(f"No project {a.name}", file=sys.stderr)
        return 2
    opts = {"use_llm": not a.no_llm, "auto_ignore": a.auto_ignore, "compressed": not a.no_compressed}
    try:
        if a.stage:
            kw = {k: v for k, v in opts.items() if k in STAGE_KW.get(a.stage, ())}
            run_stage(pr, a.stage, **kw)
        else:
            run_all(pr, from_stage=a.from_stage, **opts)
    except StageError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
