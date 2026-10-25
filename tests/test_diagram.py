"""Schematic diagrams: validation, every layout renders, motion hooks present, repair-first safety net."""
import re

import pytest

from narravid import checks, compose, make
from narravid.schema import LAYOUTS, content_schema, validate_scene
from narravid.sentences import all_sentences, parse_script

SCRIPT = parse_script("Part one. Photons.\nA source sends entangled photons to two detectors. "
                      "Their results are compared with the bound of two.")


def node(i, label, cue=2, **kw):
    return {"id": i, "label": label, "icon": "atom", "cue_sentence": cue, **kw}


def scene(layout="chain", **kw):
    s = {"id": "p1s1", "part": 1, "sentences": [2, 3], "template": "diagram", "title": "Photons reach detectors",
         "layout": layout,
         "nodes": [node("s", "Source", group="A"), node("d1", "Detector one", group="B"),
                   node("d2", "Detector two", 3, group="B")],
         "edges": [{"from": "s", "to": "d1", "label": "photons", "style": "flow", "cue_sentence": 2},
                   {"from": "s", "to": "d2", "label": "photons", "style": "arrow", "cue_sentence": 2},
                   {"from": "d1", "to": "d2", "label": "compared", "style": "link", "cue_sentence": 3}],
         "focus": [{"node": "d2", "cue_sentence": 3}]}
    s.update(kw)
    return s


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_renders_with_motion_hooks(cfg, layout):
    s = scene(layout)
    assert validate_scene(s, cfg) == []
    words = {2: [{"text": "source", "start": 1.0}, {"text": "detectors", "start": 2.0}],
             3: [{"text": "compared", "start": 6.0}]}
    r = compose.assign_reveals(s, {"start": 0.5, "end": 12.0}, words, offset=0.0)
    html = compose.render_scene(compose._env(), r, [], False)
    for hook in ("data-pop", "data-icondraw", "data-draw", "data-flow", "data-cam", "data-ripple"):
        assert hook in html, hook
    assert html.count('class="dg-node') == 3
    times = sorted(float(t) for t in re.findall(r'data-pop="([\d.]+)"', html))
    assert all(b - a >= 0.4 for a, b in zip(times, times[1:]))          # nodes get their own moment


def test_diagram_validation(cfg):
    bad = scene(edges=[{"from": "s", "to": "zz", "label": "", "style": "arrow", "cue_sentence": 2}])
    assert any("does not exist" in e for e in validate_scene(bad, cfg))
    assert any("2-6 nodes" in e for e in validate_scene(scene(nodes=[node("s", "Source")]), cfg))
    assert any("1-5 words" in e for e in validate_scene(scene(nodes=[node("s", "one two three four five six"),
                                                                   node("t", "x")], edges=[]), cfg))
    assert any("group A" in e for e in validate_scene(scene("versus", nodes=[node("a", "x"), node("b", "y")], edges=[]), cfg))
    schema = content_schema("diagram")
    assert "atom" in schema["properties"]["nodes"]["items"]["properties"]["icon"]["enum"]


def test_diagram_labels_are_fact_checked(cfg):
    idx = checks.ScriptIndex(all_sentences(SCRIPT))
    s = scene(nodes=[node("s", "Source"), node("d1", "Einstein's detector"), node("d2", "Detector two", 3)])
    errs = [r for r in checks.check_scene(s, idx, cfg, {}) if r["level"] == "error"]
    assert errs and errs[0]["field"] == "nodes[1].label"
    assert checks.is_schematic(s, idx)


def test_repair_keeps_the_diagram(cfg):
    s = scene(nodes=[node("s", "Source"), node("d1", "Einstein's detector"), node("d2", "Detector two", 3)])
    fixed = make.repair_scene(s, {"nodes[1].label", "edges[2].label"}, SCRIPT, cfg)
    assert fixed["template"] == "diagram"
    assert [n["id"] for n in fixed["nodes"]] == ["s", "d2"]
    assert all("d1" not in (e["from"], e["to"]) for e in fixed["edges"])
    assert make.repair_scene(s, {""}, SCRIPT, cfg) is None            # whole-scene rule: cannot cut out


def test_count_up_parts():
    assert compose.count_parts("75%") == {"prefix": "", "text": "75", "value": 75.0, "dec": 0, "sep": False, "suffix": "%"}
    assert compose.count_parts("≈1,906")["sep"] and compose.count_parts("0.08")["dec"] == 2
    assert compose.count_parts("two root two") is None
