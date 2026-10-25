import shutil

import pytest
from jsonschema import Draft202012Validator

from narravid import checks, tools
from narravid.schema import (GROUPING_SCHEMA, SCENE_SCHEMA, STORYBOARD_SCHEMA, content_schema, validate_grouping,
                             validate_scene)
from narravid.sentences import all_sentences, parse_script

SCRIPT = parse_script(
    "Part one. Results.\nRoughly seventy-five percent of the 1,906 patients responded. "
    "The CHSH bound is two, and quantum mechanics reaches two root two. "
    "Smith reported this in nineteen ninety-eight with the TP53 probe.")


def scene(**kw):
    base = {"id": "p1s1", "part": 1, "sentences": [2, 3, 4], "template": "bullet_list", "title": "Main findings",
            "items": [{"text": "75% of 1906 patients responded", "cue_sentence": 2}], "tag": "measured"}
    base.update(kw)
    return base


def run_one(sc, cfg):
    idx = checks.ScriptIndex(all_sentences(SCRIPT))
    return checks.check_scene(sc, idx, cfg, {})


def errors(res):
    return [r for r in res if r["level"] == "error"]


def test_traceable_numbers_pass(cfg):
    sc = scene(items=[{"text": "75% of 1,906 patients responded", "cue_sentence": 2},
                      {"text": "Quantum value 2√2 exceeds bound 2", "cue_sentence": 3},
                      {"text": "Smith, 1998, TP53 probe", "cue_sentence": 4}])
    assert errors(run_one(sc, cfg)) == []


def test_fabricated_number_is_flagged(cfg):
    res = run_one(scene(items=[{"text": "80% of 1906 patients responded", "cue_sentence": 2}]), cfg)
    assert any("80%" in r["message"] for r in errors(res))


def test_percent_vs_bare_number_is_flagged(cfg):
    res = run_one(scene(items=[{"text": "1906% responded", "cue_sentence": 2}]), cfg)
    assert any("not as" in r["message"] for r in errors(res))


QSCRIPT = parse_script("Part one. Q.\nAbout 245 trials were run. Efficiency was 0.08 below the target. "
                       "The violation exceeded five standard deviations by more than 3 units.")


def test_dropped_qualifiers_are_caught(cfg):
    idx = checks.ScriptIndex(all_sentences(QSCRIPT))
    bad = {"id": "p1s1", "part": 1, "sentences": [3], "template": "key_number", "title": "Efficiency",
           "value": "0.08", "label": "detection efficiency"}
    res = checks.check_scene(bad, idx, cfg, {})
    assert any(r["check"] == "qualifier" and r["level"] == "error" for r in res)
    good = {**bad, "label": "below the target efficiency"}
    assert not errors(checks.check_scene(good, idx, cfg, {}))
    approx = {**bad, "sentences": [2], "value": "245", "label": "trials"}
    res = checks.check_scene(approx, idx, cfg, {})
    assert any(r["check"] == "qualifier" and r["level"] == "warning" for r in res) and not errors(res)
    assert not [r for r in checks.check_scene({**approx, "value": "≈245"}, idx, cfg, {}) if r["check"] == "qualifier"]
    sym = {"id": "p1s2", "part": 1, "sentences": [4], "template": "bullet_list", "title": "Significance",
           "items": [{"text": "> 3 units beyond the bound", "cue_sentence": 4}]}
    assert not [r for r in checks.check_scene(sym, idx, cfg, {}) if r["check"] == "qualifier"]


def test_name_and_identifier_checks(cfg):
    res = run_one(scene(items=[{"text": "Confirmed by Einstein using BRCA1", "cue_sentence": 4}]), cfg)
    msgs = " ".join(r["message"] for r in errors(res))
    assert "Einstein" in msgs and "BRCA1" in msgs
    ok = run_one(scene(items=[{"text": "Reported by Smith with TP53", "cue_sentence": 4}]), cfg)
    assert errors(ok) == []


def test_math_numbers_checked(cfg):
    res = run_one(scene(template="equation", math=[r"S \le 2", r"S_{max} = 2\sqrt{2}", r"S = 3"], items=[]), cfg)
    errs = errors(res)
    assert len(errs) == 1 and "3" in errs[0]["message"]


@pytest.mark.skipif(not (tools.renderer_dir() / "node_modules" / "katex").exists(), reason="KaTeX not installed")
def test_katex_compile_failure_flagged(cfg):
    sb = {"scenes": [scene(template="equation", math=[r"\frac{1}{2", r"\tfrac{S}{8}"], items=[])]}
    k = checks.compile_math(sb)
    assert not k[r"\frac{1}{2"]["ok"] and k[r"\tfrac{S}{8}"]["ok"]


def test_coverage_gaps_and_overlaps():
    pt = {"id": "p1s0", "part": 1, "sentences": [1], "template": "part_title", "title": "Results"}
    ok = {"scenes": [pt, scene()]}
    assert checks.coverage(SCRIPT, ok) == []
    gap = {"scenes": [pt, scene(sentences=[2, 3])]}
    assert "no scene" in checks.coverage(SCRIPT, gap)[0]["message"]
    dup = {"scenes": [pt, scene(sentences=[2, 3]), scene(id="p1s2", sentences=[3, 4])]}
    assert any("several" in c["message"] for c in checks.coverage(SCRIPT, dup))


def test_schematic_label_rules():
    idx = checks.ScriptIndex(all_sentences(SCRIPT))
    assert checks.is_schematic({"template": "line_chart", "items": []}, idx)
    traceable = {"template": "bar_chart", "items": [{"text": "a", "value": 75}, {"text": "b", "value": 1906}]}
    assert not checks.is_schematic(traceable, idx)
    invented = {"template": "bar_chart", "items": [{"text": "a", "value": 60}, {"text": "b", "value": 1906}]}
    assert checks.is_schematic(invented, idx)


# ------------------------------------------------------------------ schema
def test_storyboard_schema_validation(cfg):
    v = Draft202012Validator(STORYBOARD_SCHEMA)
    good = {"scenes": [scene()]}
    assert not list(v.iter_errors(good))
    bad = {"scenes": [scene(template="pie_chart")]}
    assert list(v.iter_errors(bad))
    assert not list(Draft202012Validator(SCENE_SCHEMA).iter_errors(scene(math=["x"])))


def test_semantic_rules(cfg):
    assert validate_scene(scene(), cfg) == []
    errs = validate_scene(scene(items=[{"text": "w " * 13, "cue_sentence": 2}] * 7, sentences=[2, 4]), cfg)
    joined = " ".join(errs)
    assert "consecutive" in joined and "more than 12 words" in joined and "more than 6 items" in joined
    errs = validate_scene(scene(items=[{"text": "x", "cue_sentence": 9}]), cfg)
    assert any("cue_sentence 9" in e for e in errs)
    assert validate_scene(scene(template="key_number", items=[]), cfg) == ["key_number needs value and label"]


def test_grouping_validation():
    assert validate_grouping([{"sentences": [2, 3]}, {"sentences": [4]}], [2, 3, 4], 5) == []
    assert validate_grouping([{"sentences": [2]}, {"sentences": [4]}], [2, 3, 4], 5)
    assert validate_grouping([{"sentences": [2, 3, 4]}], [2, 3, 4], 2)


def test_content_schemas_are_valid_json_schema():
    for t in ["title_card", "bullet_list", "key_number", "equation", "comparison", "table", "timeline",
              "number_line", "bar_chart", "line_chart", "flow", "cards_grid", "quote_or_definition", "recap"]:
        Draft202012Validator.check_schema(content_schema(t))
    Draft202012Validator.check_schema(GROUPING_SCHEMA)
