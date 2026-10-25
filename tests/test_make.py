"""The one-button flow: automatic accuracy fixes and the full run."""
import pytest

from narravid import checks, make
from narravid.sentences import all_sentences, parse_script

SCRIPT = parse_script("Part one. Efficiency.\nBear in mind that the detection efficiency was 0.08 below the target. "
                      "It improved later.")


def test_plain_scene_is_always_traceable(cfg):
    bad = {"id": "p1s1", "part": 1, "sentences": [2, 3], "template": "key_number", "title": "Efficiency",
           "value": "0.08", "label": "detection efficiency", "tag": "warning"}
    idx = checks.ScriptIndex(all_sentences(SCRIPT))
    assert [r for r in checks.check_scene(bad, idx, cfg, {}) if r["level"] == "error"]
    for level in (0, 1):
        safe = make.plain_scene(bad, SCRIPT, cfg, level)
        assert safe["sentences"] == [2, 3]
        assert not [r for r in checks.check_scene(safe, idx, cfg, {}) if r["level"] == "error"], safe


def test_job_progress_and_eta():
    j = make.Job("My talk!.txt")
    assert j.name == "My talk"
    j.estimates = {"listen": 10, "plan": 20, "facts": 10, "build": 60}
    j.step, j.step_progress = 3, 0.5
    assert j.overall() == pytest.approx(0.7)
    assert j.eta_seconds() == 30


def test_transcript_becomes_script_with_parts():
    words = [{"w": w} for w in ("Part 1. Bell inequalities in brief. In 1964, John Bell showed a bound. "
                                "Part two, What the experiments measured. Aspect tested it.").split()]
    sc = parse_script(make.transcript_to_script(words))
    assert [(p["part"], p["title"]) for p in sc["parts"]] == [(1, "Bell inequalities in brief"),
                                                              (2, "What the experiments measured")]


def test_part_mentioned_mid_narration_is_not_a_heading():
    words = [{"w": w} for w in ("Part 3. Reading it. Part 2, as we saw, gave the prediction. "
                                "The limit is exceeded.").split()]
    text = make.transcript_to_script(words)
    assert "Part 2, as we saw, gave the prediction." in text          # wording untouched
    assert [p["part"] for p in parse_script(text)["parts"]] == [3]


@pytest.mark.slow
def test_recording_only_video(fixtures, tmp_path, monkeypatch):
    """Just the recording: the transcript becomes the script; parts and slides still work."""
    monkeypatch.setattr(make, "OUTPUT_DIR", tmp_path / "out")
    job = make.make_video(fixtures / "sample.wav", smart=False, log=lambda m: None)
    assert job.status == "done", job.error
    assert (tmp_path / "out" / "sample.mp4").exists()
    srt = (tmp_path / "out" / "sample.srt").read_text(encoding="utf-8")
    assert "John Bell" in srt and "Part 3" in srt
    assert abs(job.result["duration"] - 60.4) < 0.3


@pytest.mark.slow
def test_one_button_video(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(make, "OUTPUT_DIR", tmp_path / "out")
    job = make.make_video(fixtures / "sample_offscript.wav", fixtures / "sample_script.txt", smart=False,
                          log=lambda m: None)
    assert job.status == "done", job.error
    assert (tmp_path / "out" / "sample_script.mp4").exists() and (tmp_path / "out" / "sample_script.srt").exists()
    assert abs(job.result["duration"] - 68.8) < 0.3         # narration used exactly as recorded (no cuts)
    assert not job.work.exists()                              # nothing kept besides the results
