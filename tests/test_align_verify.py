"""Alignment + insertion/repetition/omission detection on synthetic recogniser output,
plus the real-audio fixtures (local Windows TTS) when the Whisper model is installed."""
import json
from pathlib import Path

import pytest

from conftest import asr_from_text
from narravid import align, config, verify
from narravid.sentences import parse_script

SCRIPT = ("Part one. Intro.\nThe bound is two. Quantum theory predicts two root two. "
          "Experiments with photons confirmed the violation clearly. Local realism fails here.")


def _script():
    return parse_script(SCRIPT)


def test_alignment_exact_and_monotonic():
    sc = _script()
    asr = asr_from_text("Part 1. Intro. The bound is 2. Quantum theory predicts 2√2. "
                        "Experiments with photons confirmed the violation clearly. Local realism fails here.")
    res = align.align(sc, asr, duration=asr[-1]["e"] + 1)
    assert res["report"]["words_matched_pct"] == 100.0
    assert res["report"]["checks"] == []
    starts = [s["start"] for s in res["sentences"]]
    assert starts == sorted(starts)
    # "two root two" (3 script words) matched the single recognised token "2√2"
    w = [x for x in res["words"] if x["text"] in ("two", "root", "two.")]
    assert all(x["matched"] for x in w)


def test_unmatched_words_are_interpolated():
    sc = _script()
    text = ("Part one. Intro. The bound is two. Quantum theory predicts two root two. "
            "Experiments with XXXX YYYY the violation clearly. Local realism fails here.")
    res = align.align(sc, asr_from_text(text), duration=30)
    ws = res["words"]
    k = [i for i, x in enumerate(ws) if x["text"] == "photons"][0]
    assert not ws[k]["matched"]
    assert ws[k - 1]["end"] <= ws[k]["start"] <= ws[k + 1]["start"]


def test_detects_insertion(cfg):
    sc = _script()
    text = ("Part one. Intro. The bound is two. Please like and subscribe to this channel for more content today. "
            "Quantum theory predicts two root two. Experiments with photons confirmed the violation clearly. "
            "Local realism fails here.")
    asr = asr_from_text(text)
    res = align.align(sc, asr, duration=asr[-1]["e"] + 1)
    sil = [(0, 0.05)] + [(w["e"], w["e"] + 0.06) for w in asr]   # tiny gaps between all words
    issues = verify.detect(res, asr, sil, cfg)
    ins = [i for i in issues if i["type"] == "insertion"]
    assert len(ins) == 1 and "subscribe" in ins[0]["spoken_text"]
    assert ins[0]["cut"]["ok"]
    assert not [i for i in issues if i["type"] != "insertion"]


def test_detects_repetition_with_both_ranges(cfg):
    sc = _script()
    text = ("Part one. Intro. The bound is two. Quantum theory predicts two root two. "
            "Experiments with photons confirmed the violation clearly. "
            "Experiments with photons confirmed the violation clearly. Local realism fails here.")
    asr = asr_from_text(text)
    res = align.align(sc, asr, duration=asr[-1]["e"] + 1)
    sil = [(w["e"], w["e"] + 0.06) for w in asr]
    issues = verify.detect(res, asr, sil, cfg)
    rep = [i for i in issues if i["type"] == "repetition"]
    assert len(rep) == 1
    (a0, a1), (b0, b1) = rep[0]["occurrences"]
    assert a1 <= b0                                     # two disjoint, ordered time ranges
    assert rep[0]["sentences"] == [4]
    assert rep[0]["cut_first"]["ok"] and rep[0]["cut_second"]["ok"]
    assert rep[0]["cut_first"]["end"] <= rep[0]["cut_second"]["start"] + 1e-6


def test_detects_omission(cfg):
    sc = _script()
    asr = asr_from_text("Part one. Intro. The bound is two. Experiments with photons confirmed the violation "
                        "clearly. Local realism fails here.")
    res = align.align(sc, asr, duration=asr[-1]["e"] + 1)
    issues = verify.detect(res, asr, [], cfg)
    om = [i for i in issues if i["type"] == "omission"]
    assert len(om) == 1 and om[0]["sentences"] == [3]


def test_plan_requires_decisions_and_refuses_unsnapped_cuts():
    iss = [{"id": 1, "type": "insertion", "choices": verify.CHOICES["insertion"], "decision": None,
            "cut": {"ok": True, "start": 1, "end": 2}, "spoken_text": "x"}]
    with pytest.raises(ValueError):
        verify.plan(iss)
    iss[0]["decision"] = "cut"
    iss[0]["cut"] = {"ok": False, "start": 1, "end": 2, "reason": "no silence"}
    with pytest.raises(ValueError):
        verify.plan(iss)


def test_edit_script_insert_and_remove():
    sc = _script()
    text = verify.edit_script(sc, [{"after_sentence": 2, "text": "and that matters"}], removes=[4])
    s2 = parse_script(text)
    texts = [s["text"] for p in s2["parts"] for s in p["sentences"]]
    assert "And that matters." in texts
    assert "Experiments with photons confirmed the violation clearly." not in texts
    assert texts[0] == "Part one. Intro."


# ------------------------------------------------------------------ real audio (local TTS fixture)
def _whisper_available():
    c = config.load()
    return (config.resolve(c["asr"]["model_dir"]) / c["asr"]["model"] / "model.bin").exists()


@pytest.mark.slow
@pytest.mark.skipif(not _whisper_available(), reason="Whisper model not installed")
def test_real_audio_offscript_detection(tmp_path, fixtures, cfg):
    from narravid import asr, audio
    wav = tmp_path / "a.wav"
    audio.to_analysis_wav(fixtures / "sample_offscript.wav", wav)
    words = asr.transcribe(wav, cfg, log=lambda m: None)
    sc = parse_script((fixtures / "sample_script.txt").read_text(encoding="utf-8"))
    from narravid import tools
    res = align.align(sc, words, tools.ffprobe_duration(wav))
    assert res["report"]["words_matched_pct"] > 90
    sil = audio.detect_silences(wav)
    issues = verify.detect(res, words, sil, cfg)
    kinds = sorted(i["type"] for i in issues)
    assert kinds == ["insertion", "repetition"], json.dumps(issues, indent=1)[:2000]
    ins = next(i for i in issues if i["type"] == "insertion")
    assert "subscribe" in ins["spoken_text"].lower() and ins["cut"]["ok"]
    rep = next(i for i in issues if i["type"] == "repetition")
    assert "authors interpret" in rep["script_text"].lower() and rep["cut_second"]["ok"]
