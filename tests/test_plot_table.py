import math

import numpy as np
import pytest

from narravid import compose, design, plot
from narravid.config import load
from narravid.schema import validate_grouping, validate_scene
from narravid.storyboard import scene_hint, visual_hint, weak_scene


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "x.real", "open('f')", "lambda: 1",
                                  "[x for x in y]", "y + 1", "sin(x, 2)", "'a'"])
def test_formula_whitelist_rejects_code(expr):
    with pytest.raises(plot.PlotError):
        plot.evaluate(expr, np.zeros(3))


def test_formula_values():
    xs = np.array([0.0, math.pi / 4])
    assert np.allclose(plot.evaluate("2*sqrt(2)*cos(x)", xs), [2 * math.sqrt(2), 2.0])
    assert np.allclose(plot.evaluate("x^2 - 1", np.array([3.0])), [8.0])
    assert plot.number("2*pi") == pytest.approx(2 * math.pi)


def chsh_scene():
    return {"id": "p1s1", "part": 1, "sentences": [12, 13], "template": "plot", "title": "Quantum beats the bound",
            "axis": {"x_label": "angle", "y_label": "correlation"},
            "plot": {"x_min": 0, "x_max": "pi",
                     "curves": [{"expr": "2*sqrt(2)*cos(x)", "label": "Quantum", "cue_sentence": 12}],
                     "marks": [{"kind": "hline", "x": None, "y": 2, "label": "Classical bound", "cue_sentence": 13}]}}


def test_plot_scene_validates_and_lays_out():
    s = chsh_scene()
    assert validate_scene(s, load()) == []
    s["plot"]["curves"][0]["_t"], s["plot"]["marks"][0]["_t"] = 1.0, 2.0
    g = plot.geometry(s, design.SERIES)
    assert g["curves"][0]["d"].startswith("M")
    assert [t["label"] for t in g["xticks"]] == ["0", "π/2", "π"]
    box = g["box"]
    assert box["t"] < g["marks"][0]["y"] < box["b"]            # the bound sits inside the plot


def test_bad_plot_is_rejected():
    s = chsh_scene()
    s["plot"]["curves"][0]["expr"] = "import os"
    assert any("not valid" in e for e in validate_scene(s, load()))


def test_pole_breaks_the_path():
    s = chsh_scene()
    s["plot"] = {"x_min": -2, "x_max": 2, "marks": [], "curves": [{"expr": "1/x", "label": "", "cue_sentence": 12, "_t": 1}]}
    d = plot.geometry(s, design.SERIES)["curves"][0]["d"]
    assert d.count("M") == 2                                    # two branches, no vertical line through the pole


def test_hints_route_spoken_maths():
    assert visual_hint("Answer pair 00 with probability 1, row reads 1 0 0 0.") == "table"
    assert visual_hint("B equals E sub AB for 00 plus 1, plus EAB for 01 minus 1.") == "equation"
    assert visual_hint("Seventy-five percent.") == "key_number"
    assert visual_hint("Here is what they do.") is None
    sents = [{"id": 1, "text": "Row 00 reads 1 0 0 0."}, {"id": 2, "text": "Row 01 reads 0 1 0 0."}]
    assert scene_hint(sents) == "table"
    assert weak_scene({"template": "spoken"}, sents) == "table"
    assert weak_scene({"template": "diagram"}, sents) is None


def test_table_scenes_may_run_longer():
    ids = list(range(1, 9))
    assert validate_grouping([{"sentences": ids, "template": "table", "title": "t"}], ids, 5) == []
    assert validate_grouping([{"sentences": ids, "template": "bullet_list", "title": "t"}], ids, 5)


def test_table_row_highlights():
    words = {7: [{"start": 10.0}], 8: [{"start": 14.0}]}
    tb = {"_t": 9.5, "rows": [["a"], ["b"], ["c"]], "row_cues": [7, 8]}
    rows = compose.table_row_times(tb, words, lambda t: t, 30.0)
    assert rows[0]["hl"] == 10.0 and rows[0]["hl_end"] == 14.0
    assert rows[1]["hl_end"] == 30.0
    assert "hl" not in rows[2]
