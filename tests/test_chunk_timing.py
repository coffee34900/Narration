"""Regression: "-t value must be non-negative" — a render chunk with negative length.

Caused by a repeated part number (e.g. a transcript saying "as we saw in Part 2" after Part 3)
or by many scenes crowded at the very end of the audio.
"""
from narravid import compose
from narravid.sentences import parse_script


def test_part_number_that_does_not_count_up_is_a_sentence():
    sc = parse_script("Part 1. Intro. One.\n\nPart 2. Methods. Two.\n\nPart 3. Results. Three.\n\n"
                      "Part 2. As we saw earlier, it holds.\n\nPart 4. End. Four.")
    assert [p["part"] for p in sc["parts"]] == [1, 2, 3, 4]
    results = next(p for p in sc["parts"] if p["part"] == 3)
    assert any("As we saw earlier" in s["text"] and not s["heading"] for s in results["sentences"])


def _windows(starts, duration, fps=30):
    scenes = [{"id": f"s{k}", "sentences": [k]} for k in range(len(starts))]
    sents = [{"id": k, "start": t} for k, t in enumerate(starts)]
    return compose.scene_windows({"scenes": scenes}, sents, duration, fps)


def test_scene_windows_stay_inside_the_audio_and_increase():
    # last five sentences all interpolated to the very end of a 10 s file
    w = _windows([0.0, 2.0, 4.0, 9.99, 9.99, 9.99, 9.99, 9.99], duration=10.0)
    starts = [x["start"] for x in w]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert all(0 <= x["start"] < x["end"] <= 10.0 for x in w)


def test_scene_windows_with_more_scenes_than_half_seconds():
    w = _windows([0.0] * 50, duration=5.0)
    assert all(x["end"] > x["start"] for x in w) and w[-1]["end"] <= 5.0
