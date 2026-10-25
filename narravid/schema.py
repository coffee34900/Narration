"""Storyboard schema: one unified scene shape + per-template content schemas.

A scene never contains HTML/CSS/JS — only template name, short text, LaTeX and numbers.

Unified item: {"text", "cue_sentence", "head"?, "when"?, "value"?, "to"?, "shape"?}
  bullet_list / recap / flow / cards_grid(head) / timeline(when) / bar_chart(value)
  number_line(value, to = range end) / line_chart(shape) / equation (items = notes)
"""
from __future__ import annotations

import re

TEMPLATES = ["part_title", "diagram", "spoken", "title_card", "bullet_list", "key_number", "equation", "comparison", "table",
             "timeline", "number_line", "bar_chart", "line_chart", "plot", "flow", "cards_grid",
             "quote_or_definition", "recap"]
LAYOUTS = ["chain", "cycle", "hub", "versus", "layers", "tree"]
EDGE_STYLES = ["flow", "arrow", "link", "becomes", "opposes"]
# "spoken" (kinetic typography of the narration itself) is the safe fallback, not an LLM choice
LLM_TEMPLATES = [t for t in TEMPLATES if t not in ("part_title", "spoken")]
TAGS = ["measured", "interpretation", "commentary", "warning", "none"]
SHAPES = ["rising", "falling", "peak", "saturating", "linear", "oscillating", "flat", "u_shaped"]

TEMPLATE_HELP = {
    "diagram": "an animated schematic of the concept: the things involved (2-6 nodes with icons) and how they "
               "connect (flows, causes, transformations, opposition). Use for mechanisms, processes, experiments, "
               "systems, causes and effects, comparisons of two sides — most scenes",
    "title_card": "a heading + one-line subtitle; for framing / transition sentences",
    "bullet_list": "2-6 short points revealed as they are mentioned",
    "key_number": "one striking number from the text with a short label",
    "equation": "a formula the sentences state or spell out in words (\"E sub AB plus ... equals 2\"), built up step by step in LaTeX",
    "comparison": "2-3 columns contrasting options/groups/models, each with short points",
    "table": "a grid of values the sentences read out (rows/columns, probabilities, outcomes, truth tables; max 8 rows x 6 columns). Use it whenever the narration walks through rows, columns or cells",
    "timeline": "dated events in order (years/dates from the text)",
    "number_line": "values or ranges placed on one axis (thresholds, intervals, bounds)",
    "bar_chart": "a few quantities from the text compared as bars (values must be in the text)",
    "plot": "an accurate function plot computed from a formula (physics/maths: waves, decays, potentials, "
            "distributions, correlations vs angle), with marked bounds, thresholds and points",
    "line_chart": "a qualitative trend (rising, saturating, peak...) — always shown as schematic",
    "flow": "a process / causal chain as 2-6 boxes with arrows",
    "cards_grid": "2-6 parallel concepts, each a short head + one line",
    "quote_or_definition": "a definition of a term or a verbatim quote from the sentences",
    "recap": "summary of the part's key points (usually at the end of a part)",
}

_item = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "cue_sentence": {"type": "integer"},
        "head": {"type": "string"},
        "when": {"type": "string"},
        "value": {"type": ["number", "string"]},
        "to": {"type": ["number", "null"]},
        "shape": {"type": "string", "enum": SHAPES},
    },
    "required": ["text", "cue_sentence"],
}

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "part": {"type": "integer"},
        "sentences": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
        "template": {"type": "string", "enum": TEMPLATES},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "items": {"type": "array", "items": _item},
        "columns": {"type": "array", "items": {
            "type": "object", "properties": {"heading": {"type": "string"}, "items": {"type": "array", "items": _item}},
            "required": ["heading", "items"]}},
        "table": {"type": "object", "properties": {
            "headers": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "cue_sentence": {"type": "integer"},
            "row_cues": {"type": "array", "items": {"type": "integer"}}}},
        "value": {"type": "string"},
        "label": {"type": "string"},
        "math": {"type": "array", "items": {"type": "string"}},
        "math_cues": {"type": "array", "items": {"type": "integer"}},
        "quote": {"type": "object", "properties": {"text": {"type": "string"}, "attribution": {"type": "string"}}},
        "axis": {"type": "object", "properties": {"min": {"type": "number"}, "max": {"type": "number"},
                                                  "unit": {"type": "string"}, "x_label": {"type": "string"},
                                                  "y_label": {"type": "string"}}},
        "unit": {"type": "string"},
        "plot": {"type": "object"},
        "layout": {"type": "string", "enum": LAYOUTS},
        "nodes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "label": {"type": "string"}, "icon": {"type": "string"},
            "note": {"type": "string"}, "group": {"type": "string"}, "cue_sentence": {"type": "integer"}},
            "required": ["id", "label", "cue_sentence"]}},
        "edges": {"type": "array", "items": {"type": "object", "properties": {
            "from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"},
            "style": {"type": "string", "enum": EDGE_STYLES}, "cue_sentence": {"type": "integer"}},
            "required": ["from", "to", "cue_sentence"]}},
        "focus": {"type": "array", "items": {"type": "object", "properties": {
            "node": {"type": "string"}, "cue_sentence": {"type": "integer"}}, "required": ["node", "cue_sentence"]}},
        "tag": {"type": "string", "enum": TAGS},
        "source": {"type": "string"},
        "approved": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["part", "sentences", "template", "title"],
}

STORYBOARD_SCHEMA = {
    "type": "object",
    "properties": {"version": {"type": "integer"}, "scenes": {"type": "array", "items": SCENE_SCHEMA}},
    "required": ["scenes"],
}

# ----------------------------------------------------------------- LLM step 1: grouping
GROUPING_SCHEMA = {
    "type": "object",
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "sentences": {"type": "array", "items": {"type": "integer"}},
            "template": {"type": "string", "enum": LLM_TEMPLATES},
            "title": {"type": "string"},
            "tag": {"type": "string", "enum": TAGS},
        },
        "required": ["sentences", "template", "title", "tag"]}}},
    "required": ["scenes"],
}


def _llm_item(*extra: str, required_extra: tuple[str, ...] = ()) -> dict:
    props = {"text": {"type": "string"}, "cue_sentence": {"type": "integer"}}
    for e in extra:
        props[e] = _item["properties"][e]
    return {"type": "object", "properties": props, "required": ["text", "cue_sentence", *required_extra]}


def content_schema(template: str, max_items: int = 6) -> dict:
    """JSON schema the LLM must fill for one scene of `template`."""
    items = lambda it, lo=1: {"type": "array", "items": it, "minItems": lo, "maxItems": max_items}  # noqa: E731
    base = {"title": {"type": "string"}}
    req = ["title"]
    t = template
    if t == "diagram":
        from .icons import ICONS
        base["layout"] = {"type": "string", "enum": LAYOUTS}
        base["nodes"] = {"type": "array", "minItems": 2, "maxItems": 6, "items": {
            "type": "object", "properties": {
                "id": {"type": "string"}, "label": {"type": "string"},
                "icon": {"type": "string", "enum": sorted(ICONS)},
                "group": {"type": "string", "enum": ["A", "B", ""]},
                "cue_sentence": {"type": "integer"}},
            "required": ["id", "label", "icon", "cue_sentence"]}}
        base["edges"] = {"type": "array", "maxItems": 7, "items": {
            "type": "object", "properties": {
                "from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"},
                "style": {"type": "string", "enum": EDGE_STYLES}, "cue_sentence": {"type": "integer"}},
            "required": ["from", "to", "label", "style", "cue_sentence"]}}
        req += ["layout", "nodes", "edges"]
    elif t == "title_card":
        base["subtitle"] = {"type": "string"}
        req.append("subtitle")
    elif t in ("bullet_list", "recap"):
        base["items"] = items(_llm_item())
        req.append("items")
    elif t == "flow":
        base["items"] = items(_llm_item(), 2)
        req.append("items")
    elif t == "cards_grid":
        base["items"] = items(_llm_item("head", required_extra=("head",)), 2)
        req.append("items")
    elif t == "timeline":
        base["items"] = items(_llm_item("when", required_extra=("when",)), 2)
        req.append("items")
    elif t == "key_number":
        base.update({"value": {"type": "string"}, "label": {"type": "string"}})
        req += ["value", "label"]
    elif t == "equation":
        base["math"] = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}
        base["math_cues"] = {"type": "array", "items": {"type": "integer"}, "maxItems": 5}
        base["items"] = {"type": "array", "items": _llm_item(), "maxItems": 3}
        req += ["math"]
    elif t == "comparison":
        col = {"type": "object", "properties": {"heading": {"type": "string"},
                                                "items": {"type": "array", "items": _llm_item(), "minItems": 1, "maxItems": 3}},
               "required": ["heading", "items"]}
        base["columns"] = {"type": "array", "items": col, "minItems": 2, "maxItems": 3}
        req.append("columns")
    elif t == "table":
        base["table"] = {"type": "object", "properties": {
            "headers": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 6},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "minItems": 1, "maxItems": 8},
            "cue_sentence": {"type": "integer"},
            "row_cues": {"type": "array", "items": {"type": "integer"}, "maxItems": 8}},
            "required": ["headers", "rows", "cue_sentence", "row_cues"]}
        req.append("table")
    elif t == "number_line":
        base["axis"] = {"type": "object", "properties": {"min": {"type": "number"}, "max": {"type": "number"},
                                                         "unit": {"type": "string"}}, "required": ["min", "max", "unit"]}
        base["items"] = items({"type": "object", "properties": {
            "text": {"type": "string"}, "cue_sentence": {"type": "integer"}, "value": {"type": "number"},
            "to": {"type": ["number", "null"]}}, "required": ["text", "cue_sentence", "value", "to"]})
        req += ["axis", "items"]
    elif t == "bar_chart":
        base["unit"] = {"type": "string"}
        base["items"] = items({"type": "object", "properties": {
            "text": {"type": "string"}, "cue_sentence": {"type": "integer"}, "value": {"type": "number"}},
            "required": ["text", "cue_sentence", "value"]}, 2)
        req += ["unit", "items"]
    elif t == "line_chart":
        base["axis"] = {"type": "object", "properties": {"x_label": {"type": "string"}, "y_label": {"type": "string"}},
                        "required": ["x_label", "y_label"]}
        base["items"] = {"type": "array", "minItems": 1, "maxItems": 3, "items": _llm_item("shape", required_extra=("shape",))}
        req += ["axis", "items"]
    elif t == "plot":
        bound = {"type": ["number", "string"]}
        base["axis"] = {"type": "object", "properties": {"x_label": {"type": "string"}, "y_label": {"type": "string"}},
                        "required": ["x_label", "y_label"]}
        base["plot"] = {"type": "object", "properties": {
            "x_min": bound, "x_max": bound,
            "curves": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object", "properties": {
                "expr": {"type": "string"}, "label": {"type": "string"}, "cue_sentence": {"type": "integer"}},
                "required": ["expr", "label", "cue_sentence"]}},
            "marks": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["hline", "vline", "point"]},
                "x": {"type": ["number", "string", "null"]}, "y": {"type": ["number", "string", "null"]},
                "label": {"type": "string"}, "cue_sentence": {"type": "integer"}},
                "required": ["kind", "x", "y", "label", "cue_sentence"]}}},
            "required": ["x_min", "x_max", "curves", "marks"]}
        req += ["axis", "plot"]
    elif t == "quote_or_definition":
        base["quote"] = {"type": "object", "properties": {"text": {"type": "string"}, "attribution": {"type": "string"}},
                         "required": ["text", "attribution"]}
        req.append("quote")
    return {"type": "object", "properties": base, "required": req}


# ----------------------------------------------------------------- semantic validation
def words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if w])


def scene_items(scene: dict) -> list[dict]:
    out = list(scene.get("items") or [])
    for c in scene.get("columns") or []:
        out += c.get("items") or []
    return out


def validate_scene(scene: dict, cfg: dict, part_sentence_ids: list[int] | None = None) -> list[str]:
    """Hard rules (errors). Used both for LLM retries and for hand-edited scenes."""
    sb = cfg["storyboard"]
    errs: list[str] = []
    t = scene.get("template")
    sids = scene.get("sentences") or []
    if t not in TEMPLATES:
        errs.append(f"unknown template {t}")
    if sids != sorted(sids) or (sids and sids != list(range(sids[0], sids[-1] + 1))):
        errs.append("sentences must be consecutive ids in order")
    if part_sentence_ids is not None and any(s not in part_sentence_ids for s in sids):
        errs.append(f"sentence ids {sids} are not all in this part")
    if words(scene.get("title", "")) > sb["max_title_words"]:
        errs.append(f"title has more than {sb['max_title_words']} words")
    its = scene_items(scene)
    for it in its:
        if it.get("cue_sentence") not in sids:
            errs.append(f"item '{it.get('text', '')[:30]}' has cue_sentence {it.get('cue_sentence')} "
                        f"which is not one of this scene's sentences {sids}")
        if words(it.get("text", "")) > sb["max_words_per_item"]:
            errs.append(f"item '{it.get('text', '')[:40]}…' has more than {sb['max_words_per_item']} words")
        if not str(it.get("text", "")).strip():
            errs.append("empty item text")
    if len(scene.get("items") or []) > sb["max_items"]:
        errs.append(f"more than {sb['max_items']} items")
    if t == "comparison":
        cols = scene.get("columns") or []
        if not 2 <= len(cols) <= 3:
            errs.append("comparison needs 2-3 columns")
        if sum(len(c.get("items") or []) for c in cols) > 9:
            errs.append("comparison has too many points (max 3 per column)")
    if t == "key_number" and not (scene.get("value") and scene.get("label")):
        errs.append("key_number needs value and label")
    if t == "equation" and not scene.get("math"):
        errs.append("equation needs at least one LaTeX string in math")
    if t == "table":
        tb = scene.get("table") or {}
        hs, rows = tb.get("headers") or [], tb.get("rows") or []
        if not hs or not rows:
            errs.append("table needs headers and rows")
        for r in rows:
            if len(r) != len(hs):
                errs.append("every table row must have one cell per header")
                break
        if len(rows) > 8:
            errs.append("table has more than 8 rows")
        if len(hs) > 6:
            errs.append("table has more than 6 columns")
        for c in tb.get("row_cues") or []:
            if c not in sids:
                errs.append(f"table row_cues value {c} is not one of this scene's sentences {sids}")
                break
        if tb.get("cue_sentence") is not None and tb.get("cue_sentence") not in sids:
            errs.append("table cue_sentence not in scene sentences")
    if t in ("bar_chart", "number_line"):
        for it in scene.get("items") or []:
            try:
                float(it.get("value"))
            except (TypeError, ValueError):
                errs.append(f"{t} item '{it.get('text', '')[:30]}' needs a numeric value")
    if t == "number_line":
        ax = scene.get("axis") or {}
        if not isinstance(ax.get("min"), (int, float)) or not isinstance(ax.get("max"), (int, float)) \
                or ax["min"] >= ax["max"]:
            errs.append("number_line needs axis.min < axis.max")
    if t in ("flow", "cards_grid", "timeline", "bar_chart") and len(scene.get("items") or []) < 2:
        errs.append(f"{t} needs at least 2 items")
    if t == "timeline":
        whens = [str(it.get("when", "")).strip() for it in scene.get("items") or []]
        if len(set(whens)) != len(whens):
            errs.append("timeline events need different dates (each 'when' must be distinct)")
    if t in ("bullet_list", "recap") and not scene.get("items"):
        errs.append(f"{t} needs at least 1 item")
    if t == "quote_or_definition" and not (scene.get("quote") or {}).get("text"):
        errs.append("quote_or_definition needs quote.text")
    if t == "line_chart" and not scene.get("items"):
        errs.append("line_chart needs at least one curve item with a shape")
    if t == "diagram":
        errs += _validate_diagram(scene, sids)
    if t == "plot":
        from .plot import validate as validate_plot
        errs += validate_plot(scene)
        p = scene.get("plot") or {}
        for part in (p.get("curves") or []) + (p.get("marks") or []):
            if part.get("cue_sentence") not in sids:
                errs.append(f"plot '{part.get('label', '')[:30]}' cue_sentence {part.get('cue_sentence')} not in {sids}")
            if words(part.get("label", "")) > 5:
                errs.append(f"plot label '{part.get('label', '')[:30]}' must be at most 5 words")
    return errs


def _validate_diagram(scene: dict, sids: list[int]) -> list[str]:
    errs = []
    nodes, edges = scene.get("nodes") or [], scene.get("edges") or []
    layout = scene.get("layout") or "chain"
    ids = [n.get("id") for n in nodes]
    if layout not in LAYOUTS:
        errs.append(f"diagram layout must be one of {LAYOUTS}")
    if not 2 <= len(nodes) <= (7 if layout == "hub" else 6):
        errs.append("a diagram needs 2-6 nodes")
    if len(set(ids)) != len(ids):
        errs.append("node ids must be unique")
    for n in nodes:
        if words(n.get("label", "")) > 5 or not n.get("label", "").strip():
            errs.append(f"node label '{n.get('label', '')[:30]}' must be 1-5 words")
        if words(n.get("note", "")) > 8:
            errs.append(f"node note '{n.get('note', '')[:30]}' must be at most 8 words")
        if n.get("cue_sentence") not in sids:
            errs.append(f"node '{n.get('label', '')[:30]}' cue_sentence {n.get('cue_sentence')} not in {sids}")
    if len(edges) > 7:
        errs.append("a diagram has at most 7 connections")
    for e in edges:
        if e.get("from") not in ids or e.get("to") not in ids:
            errs.append(f"connection {e.get('from')}->{e.get('to')} refers to a node id that does not exist")
        elif e.get("from") == e.get("to"):
            errs.append("a connection cannot start and end at the same node")
        if words(e.get("label", "")) > 4:
            errs.append(f"connection label '{e.get('label', '')[:30]}' must be at most 4 words")
        if e.get("cue_sentence") not in sids:
            errs.append(f"connection cue_sentence {e.get('cue_sentence')} not in {sids}")
    if layout == "versus" and not ({"A", "B"} <= {n.get("group") for n in nodes}):
        errs.append("versus layout needs nodes in group A and in group B")
    for f in scene.get("focus") or []:
        if f.get("node") not in ids:
            errs.append(f"focus refers to unknown node {f.get('node')}")
    return errs


LONG_SCENE = 8


def validate_grouping(groups: list[dict], body_ids: list[int], max_per_scene: int) -> list[str]:
    errs = []
    flat = [s for g in groups for s in g.get("sentences", [])]
    if flat != body_ids:
        missing = [s for s in body_ids if s not in flat]
        extra = [s for s in flat if s not in body_ids]
        dup = sorted({s for s in flat if flat.count(s) > 1})
        if missing:
            errs.append(f"sentences not in any scene: {missing}")
        if extra:
            errs.append(f"sentence ids that do not exist in this part: {extra}")
        if dup:
            errs.append(f"sentences used in more than one scene: {dup}")
        if not (missing or extra or dup):
            errs.append("scenes must list sentences in their original order")
    for g in groups:
        s = g.get("sentences", [])
        # a table or formula walked through row by row / term by term stays on screen the whole time
        cap = max(max_per_scene, LONG_SCENE) if g.get("template") in ("table", "equation") else max_per_scene
        if len(s) > cap:
            errs.append(f"scene '{g.get('title', '')[:30]}' has {len(s)} sentences (max {cap})")
        if s and s != list(range(s[0], s[0] + len(s))):
            errs.append(f"scene '{g.get('title', '')[:30]}' sentences {s} are not consecutive")
        if not s:
            errs.append("a scene has no sentences")
    return errs
