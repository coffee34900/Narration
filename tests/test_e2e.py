"""End-to-end: ~1-minute script + local-TTS narration -> MP4 + SRT, with all QA checks.

Uses the heuristic storyboard (no LLM) so it is deterministic; the LLM path is exercised
by the GUI / `python -m narravid.pipeline run <name>`.
Run:  pytest -m slow tests/test_e2e.py
"""
import pytest

from narravid import config, tools
from narravid.pipeline import new_project, run_stage


@pytest.mark.slow
def test_end_to_end_video(fixtures, tmp_path):
    pr = new_project("e2e", fixtures / "sample_script.txt", fixtures / "sample_offscript.wav", root=tmp_path)
    logs = []
    log = logs.append
    run_stage(pr, "ingest", log)
    run_stage(pr, "align", log)
    issues = pr.read_json("issues.json")["issues"]
    assert sorted(i["type"] for i in issues) == ["insertion", "repetition"], "off-script audio must be detected"
    for i in issues:
        i["decision"] = "cut" if i["type"] == "insertion" else "cut_second"
    pr.write_json("issues.json", {"issues": issues})
    run_stage(pr, "verify", log)
    run_stage(pr, "storyboard", log, use_llm=False)
    run_stage(pr, "checks", log, use_llm=False)
    assert pr.read_json("checks.json")["ok"]
    run_stage(pr, "compose", log)
    assert pr.read_json("qa/layout.json")["ok"], pr.read_json("qa/layout.json")
    run_stage(pr, "render", log, compressed=True)
    rep = run_stage(pr, "qa", log)
    assert pr.p("render/final.mp4").exists() and pr.p("render/final.srt").exists()
    assert rep["sync"]["duration_ok"], rep["sync"]
    assert not rep["sync"]["scenes_out_of_sync"]
    assert rep["ok"]
    streams = tools.ffprobe_streams(pr.p("render/final.mp4"))
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (1920, 1080, "30/1")
    assert a["codec_name"] == "aac"
    small = list(pr.p("render/compressed").glob("*.mp4"))
    target_mb = config.load()["render"]["compressed_target_mb"]
    assert small and all(f.stat().st_size / 1048576 <= target_mb for f in small)
