import re

import pytest

from narravid import audio, captions, design, tools


def words_of(sentences):
    out, t = [], 0.0
    for sid, s in enumerate(sentences, 1):
        for w in s.split():
            out.append({"sid": sid, "text": w, "start": round(t, 2), "end": round(t + 0.25, 2)})
            t += 0.3
        t += 0.5
    return out


def test_caption_lines_respect_limits_and_sentences():
    ws = words_of(["This is a fairly long sentence, with a comma in the middle, that must wrap onto several caption lines.",
                   "Short one."])
    cues = captions.segment(ws, max_chars=50)
    assert all(len(c["text"]) <= 50 for c in cues)
    assert all(len({w["sid"] for w in ws if c["start"] <= w["start"] < c["end"]} | {c["sid"]}) == 1 for c in cues)
    assert cues[0]["text"].endswith(",")                        # breaks on punctuation
    assert cues[-1]["text"] == "Short one."
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"] + 1e-9                     # one line at a time, no overlap
    joined = " ".join(c["text"] for c in cues)
    assert joined.split() == [w["text"] for w in ws]             # exact script text


def test_srt_and_ass_format():
    cues = [{"start": 0.0, "end": 1.5, "text": "Hello world.", "sid": 1},
            {"start": 61.25, "end": 3725.5, "text": "Later {x}", "sid": 2}]
    srt = captions.to_srt(cues)
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello world.\n" in srt
    assert "01:02:05,500" in srt
    ass = captions.to_ass(cues)
    assert "Dialogue: 0,0:01:01.25,1:02:05.50,Default,,0,0,0,,Later (x)" in ass
    assert "PlayResX: 1920" in ass


def test_remap_and_merge():
    cuts = audio.merge_cuts([(10, 12), (11, 13), (20, 21)])
    assert cuts == [(10, 13), (20, 21)]
    assert audio.remap_time(5, cuts) == 5
    assert audio.remap_time(11, cuts) is None
    assert audio.remap_time(15, cuts) == 12
    assert audio.remap_time(25, cuts) == 21


def test_snap_cut_requires_silence():
    sil = [(9.8, 10.2), (14.6, 15.0)]
    ok = audio.snap_cut(10.1, 14.7, sil, window=1.0)
    assert ok["ok"] and ok["start"] == 10.0 and ok["end"] == 14.8
    bad = audio.snap_cut(10.1, 18.0, sil, window=1.0)
    assert not bad["ok"]


def test_apply_cuts_on_real_audio(tmp_path):
    ff = tools.find("ffmpeg")
    src = tmp_path / "tone.wav"
    tools.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=10", "-ar", "48000", src])
    dst = tmp_path / "cut.wav"
    audio.apply_cuts(src, dst, [(2.0, 3.5), (6.0, 7.0)])
    assert abs(tools.ffprobe_duration(dst) - 7.5) < 0.01


def test_align_words_remapped_after_cut():
    from narravid.align import remap_asr
    asr = [{"w": "a", "s": 1.0, "e": 1.2}, {"w": "inserted", "s": 2.5, "e": 2.9}, {"w": "b", "s": 4.0, "e": 4.3}]
    out = remap_asr(asr, [(2.0, 3.5)])
    assert [w["w"] for w in out] == ["a", "b"]
    assert out[1]["s"] == pytest.approx(2.5) and out[1]["e"] == pytest.approx(2.8)


@pytest.mark.parametrize("fg", ["text", "muted", "accent", "measured", "interpretation", "commentary", "warning",
                                "schematic"])
@pytest.mark.parametrize("bg", ["bg", "surface", "surface2"])
def test_palette_meets_wcag_aa(fg, bg):
    assert design.contrast(design.PALETTE[fg], design.PALETTE[bg]) >= 4.5


def test_css_minimum_font_sizes():
    css = (design.__file__.replace("design.py", "templates/design.css"))
    sizes = [int(x) for x in re.findall(r"font-size:(\d+)px", open(css, encoding="utf-8").read())]
    assert min(sizes) >= design.TYPE["min_px"]
    assert "font-size:56px" in open(css, encoding="utf-8").read()   # titles >= 44 px
