"""Every template must render from the minimum valid scene (no optional fields).

Regression: the automatic plain fallback made a title_card without a subtitle and the
strict template crashed with "'dict object' has no attribute 'subtitle'".
"""
import pytest

from narravid import compose, make
from narravid.schema import TEMPLATES
from narravid.sentences import parse_script

IT = lambda t, **kw: {"text": t, "cue_sentence": 2, **kw}  # noqa: E731
MINIMAL = {
    "part_title": {},
    "spoken": {},
    "diagram": {"layout": "chain", "nodes": [{"id": "a", "label": "Source", "icon": "sparkles", "cue_sentence": 2},
                                             {"id": "b", "label": "Detector", "icon": "nonsense", "cue_sentence": 2}],
                "edges": [{"from": "a", "to": "b", "label": "photons", "style": "flow", "cue_sentence": 2}]},
    "title_card": {},
    "bullet_list": {"items": [IT("one point")]},
    "recap": {"items": [IT("one point")]},
    "key_number": {"value": "75%", "label": "responded"},
    "equation": {"math": ["S \\le 2"]},
    "comparison": {"columns": [{"heading": "A", "items": [IT("x")]}, {"heading": "B", "items": [IT("y")]}]},
    "table": {"table": {"headers": ["a", "b"], "rows": [["1", "2"]], "cue_sentence": 2}},
    "timeline": {"items": [IT("first", when="1964"), IT("second", when="1982")]},
    "number_line": {"axis": {"min": 0, "max": 3, "unit": ""}, "items": [IT("bound", value=2, to=None)]},
    "bar_chart": {"items": [IT("a", value=2), IT("b", value=3)]},
    "line_chart": {"items": [IT("trend", shape="rising")]},
    "plot": {"axis": {"x_label": "angle", "y_label": "S"},
             "plot": {"x_min": 0, "x_max": "pi", "marks": [],
                      "curves": [{"expr": "cos(x)", "label": "bound", "cue_sentence": 2}]}},
    "flow": {"items": [IT("step one"), IT("step two")]},
    "cards_grid": {"items": [IT("text a", head="A"), IT("text b", head="B")]},
    "quote_or_definition": {"quote": {"text": "a definition", "attribution": ""}},
}


@pytest.mark.parametrize("template", TEMPLATES)
def test_minimal_scene_renders(template):
    assert set(MINIMAL) == set(TEMPLATES)
    scene = {"id": "p1s1", "part": 1, "sentences": [2], "template": template, "title": "A title", **MINIMAL[template]}
    words = {2: [{"text": "The", "start": 11.0}, {"text": "bound", "start": 11.4}, {"text": "is", "start": 11.8},
                 {"text": "2.", "start": 12.0}]}
    s = compose.assign_reveals(scene, {"start": 10.0, "end": 20.0}, words, offset=0.0)
    html = compose.render_scene(compose._env(), s, ["<span>math</span>"] if template == "equation" else [], False)
    if template == "spoken":          # kinetic typography shows the narration, not a title
        assert html.count("data-lit") == 4 and 'class="sp-w key"' in html
    else:
        assert "A title" in html


@pytest.mark.parametrize("level", [0, 1])
def test_plain_fallback_scenes_render(cfg, level):
    script = parse_script("Part one. Results.\nThe bound is two. It was exceeded.")
    for template in ("key_number", "part_title", "bullet_list"):
        bad = {"id": "p1s1", "part": 1, "sentences": [1] if template == "part_title" else [2, 3],
               "template": template, "title": "x", "value": "9", "label": "y"}
        safe = make.plain_scene(bad, script, cfg, level)
        s = compose.assign_reveals(safe, {"start": 0.0, "end": 8.0}, {}, offset=0.0)
        assert compose.render_scene(compose._env(), s, [], True)
