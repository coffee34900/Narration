"""Deterministic clean-up of what a small model writes (sentence case, diagram slips, transcript spacing)."""
from narravid import make
from narravid.schema import validate_scene
from narravid.sentences import parse_script
from narravid.storyboard import flow_to_diagram, name_words, sentence_case, sentence_case_scene, tidy_diagram

SCRIPT = parse_script("Part one. Bell.\nIn 1964, John Bell showed a bound. Aspect tested the CHSH form with photons.")


def test_sentence_case_keeps_names_and_acronyms():
    names = name_words(SCRIPT)
    assert {"Bell", "John", "CHSH"} <= names
    assert sentence_case("Bell Bound vs Local Hidden Variables", names) == "Bell bound vs local hidden variables"
    assert sentence_case("Loophole-Free Tests in 2015 by John Bell", names) == "Loophole-free tests in 2015 by John Bell"
    assert sentence_case("The CHSH Form", names) == "The CHSH form"
    s = sentence_case_scene({"title": "Two Root Two", "value": "2 root 2"}, names)
    assert s["value"] == "2√2"


def test_tidy_diagram_fixes_slips(cfg):
    s = {"id": "p1s1", "part": 1, "sentences": [2, 3], "template": "diagram", "title": "Test", "layout": "versus",
         "nodes": [{"id": "Local Realism", "label": "Local realism", "icon": "shield", "cue_sentence": 9},
                   {"id": "qm", "label": "Quantum mechanics", "icon": "particle", "cue_sentence": 3}],
         "edges": [{"from": "local-realism", "to": "qm", "label": "x", "style": "arrow", "cue_sentence": 3},
                   {"from": "violation", "to": "qm", "label": "y", "style": "arrow", "cue_sentence": 3}]}
    t = tidy_diagram(s)
    assert validate_scene(t, cfg) == []
    assert [n["group"] for n in t["nodes"]] == ["A", "B"]           # sides assigned
    assert len(t["edges"]) == 1                                       # dangling link dropped
    assert t["nodes"][1]["icon"] == "atom" and t["nodes"][0]["cue_sentence"] == 2


def test_flow_becomes_chain_diagram(cfg):
    f = {"id": "p1s1", "part": 1, "sentences": [2, 3], "template": "flow", "title": "Steps",
         "items": [{"text": "Measure", "cue_sentence": 2}, {"text": "Compare", "cue_sentence": 3}]}
    d = flow_to_diagram(f)
    assert d["template"] == "diagram" and d["layout"] == "chain" and len(d["edges"]) == 1
    assert validate_scene(d, cfg) == []


def test_transcript_keeps_whisper_spacing():
    words = [{"w": "about", "r": " about"}, {"w": "2", "r": " 2"}, {"w": ".83.", "r": ".83."},
             {"w": "loophole", "r": " loophole"}, {"w": "-free", "r": "-free"}, {"w": "75", "r": " 75"}, {"w": "%", "r": "%"}]
    assert make.transcript_to_script(words).strip() == "about 2.83. loophole-free 75%"
    no_raw = [{"w": w["w"]} for w in words]
    assert make.transcript_to_script(no_raw).strip() == "about 2.83. loophole-free 75%"
