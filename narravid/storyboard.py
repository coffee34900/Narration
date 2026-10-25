"""Stage 4 — storyboard (the only LLM stage).

Two small structured calls keep the context tiny for a small model:
  1. per part: group consecutive sentences into scenes, pick a template + title + tag
  2. per scene: fill that template's content schema
Each call is schema-constrained and semantically validated; validation errors are fed
back for up to N retries. If the LLM still fails, the scene falls back to a plain,
verbatim-derived bullet list — so a video can always be produced, and the LLM can be
skipped entirely (heuristic storyboard, then edit by hand).
"""
from __future__ import annotations

import json
import re

from .llm import LLM, LLMError
from .schema import (GROUPING_SCHEMA, LLM_TEMPLATES, TEMPLATE_HELP, content_schema, validate_grouping,
                     validate_scene)

SYSTEM = """You turn narration sentences into a storyboard for a scientific explainer video for PhD-level viewers.
You output JSON only. You never output HTML, CSS or JavaScript.

ACCURACY RULES (most important):
- Use ONLY information stated in the given sentences. Do not add facts, numbers, names, units, years or formulas.
- Every number, percentage, year, name, gene/probe name, sample size or symbol you write must appear in the sentences.
- Copy numbers exactly as the sentences give them (digits are fine: "seventy-five percent" -> "75%").
- Write LaTeX only for formulas the sentences actually state. Use KaTeX-compatible LaTeX.
- If unsure, write less. A plain correct label is better than a clever wrong one.

STYLE RULES:
- Sentence case (capitalise only the first word and proper names). No trailing full stops.
- Titles at most 8 words. Each item at most 12 words — a compressed phrase, not a full sentence.
- At most 6 items per scene.
- cue_sentence = the id of the sentence where the narration first mentions that item.
"""


def _sentences_block(sents: list[dict], hints: bool = False) -> str:
    def line(s):
        h = visual_hint(s["text"]) if hints else None
        return f"[{s['id']}] {s['text']}" + (f"   (looks like: {h})" if h else "")
    return "\n".join(line(s) for s in sents)


# ---------------------------------------------------------------------------- visual hints
# Cheap, deterministic routing: a 4B model picks templates far better when the code has
# already noticed "this sentence reads out a table" or "this sentence spells out a formula".
_TABLE_CUE = re.compile(r"\b(row|rows|column|columns|cell|cells|entry|entries|table)\b", re.I)
_MATH_CUE = re.compile(r"\b(sub|equals|plus|minus|times|squared|divided by|sum of|inequality|bound)\b|[=+−±≤≥]", re.I)
_NUMBER = re.compile(r"\d|\b(zero|one|two|three|four|half|percent)\b", re.I)


def visual_hint(text: str) -> str | None:
    """'table' / 'equation' / 'key_number' when a sentence obviously reads one out, else None."""
    nums = len(_NUMBER.findall(text))
    if _TABLE_CUE.search(text) and nums >= 2:
        return "table"
    if len(_MATH_CUE.findall(text)) >= 2 and nums >= 1:
        return "equation"
    if re.search(r"(%|\bpercent\b|\bout of\b)", text, re.I):
        return "key_number"
    return None


def scene_hint(sents: list[dict]) -> str | None:
    """The visual a scene's sentences point at (table beats equation beats number)."""
    hs = [visual_hint(s["text"]) for s in sents]
    for h in ("table", "equation", "key_number"):
        if hs.count(h) * 2 >= len(hs) or (h != "key_number" and hs.count(h) >= 2):
            return h
    return None


def grouping_prompt(part: dict, body: list[dict], max_per_scene: int) -> list[dict]:
    templates = "\n".join(f"- {t}: {TEMPLATE_HELP[t]}" for t in LLM_TEMPLATES)
    user = f"""Part {part['part']}: "{part['title']}"

Sentences (a "looks like" note is a strong hint for the template):
{_sentences_block(body, hints=True)}

Group these sentences into scenes. Rules:
- Every sentence id from {body[0]['id']} to {body[-1]['id']} must appear in exactly one scene, in order.
- Each scene is a run of consecutive sentences, 1 to {max_per_scene} sentences long
  (a "table" or "equation" scene may be up to 8, so the viewer keeps one picture while it is explained).
- Start a new scene when the topic or the kind of visual changes.
- For each scene pick the best template:
{templates}
- tag: "measured" = a result measured/observed in data; "interpretation" = what authors conclude from it;
  "commentary" = the narrator's own view; "warning" = a caveat/limitation; "none" otherwise.
- This is a MOTION-GRAPHICS video, not a slide deck. Use "diagram" for most scenes: whenever the sentences
  mention things (objects, particles, people, groups, data, models, institutions, quantities) and how they
  relate — something flows, causes, produces, measures, becomes, opposes or is compared with something else.
- Use key_number / bar_chart / timeline / number_line / table only when the sentences contain the numbers or
  dates themselves. Use equation only for a stated formula.
- When several sentences in a row read out rows, columns or cells, keep them in ONE "table" scene.
  When sentences spell out a formula term by term, keep them in ONE "equation" scene.
- Use "plot" when the sentences describe how one quantity depends on another (oscillates, decays,
  grows as, falls off as, peaks at, versus angle/time/distance) or a curve against a bound.
- Use bullet_list only when nothing can be drawn (pure opinions or lists of unrelated remarks).
- Aim for at least half of the scenes to be "diagram". Never use the same template for three scenes in a row.

Return {{"scenes": [{{"sentences": [...], "template": "...", "title": "...", "tag": "..."}}]}}"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def content_prompt(part: dict, scene: dict, sents: list[dict]) -> list[dict]:
    t = scene["template"]
    from .icons import prompt_list
    extra = {
        "diagram": (
            "Draw the concept as a schematic. nodes = the 2-6 THINGS involved, each with a 1-5 word label taken from "
            "the sentences and the best icon. edges = how they relate: style 'flow' (something moves from A to B), "
            "'arrow' (A causes/leads to/produces B), 'becomes' (A turns into B), 'opposes' (A limits/contradicts B), "
            "'link' (A is associated with B); label = 0-4 words from the sentences. "
            "layout: 'chain' (steps in order), 'cycle' (a loop that returns), 'hub' (one central thing linked to "
            "others), 'versus' (two sides compared: give nodes group 'A' or 'B'), 'layers' (stacked levels), "
            "'tree' (one cause with several consequences). Set each cue_sentence to where the narration first "
            "mentions that node or relation.\n"
            "Example for \"[4] Photons from the source reach two detectors. [5] Their results are compared.\": "
            '{"title": "Photons reach two detectors", "layout": "tree", "nodes": ['
            '{"id": "s", "label": "Source", "icon": "radio-tower", "group": "", "cue_sentence": 4}, '
            '{"id": "d1", "label": "Detector one", "icon": "satellite-dish", "group": "", "cue_sentence": 4}, '
            '{"id": "d2", "label": "Detector two", "icon": "satellite-dish", "group": "", "cue_sentence": 4}], '
            '"edges": [{"from": "s", "to": "d1", "label": "photons", "style": "flow", "cue_sentence": 4}, '
            '{"from": "s", "to": "d2", "label": "photons", "style": "flow", "cue_sentence": 4}]}\n'
            f"Icons available: {prompt_list()}."),
        "key_number": 'value = the number exactly as in the text (e.g. "75%", "1906", "0.08"); label = what it counts.',
        "equation": (
            "math = list of LaTeX strings, one per step in the order narrated. Turn spoken maths into symbols: "
            "\"E sub AB\" -> E_{AB}, \"minus\" -> -, \"less than or equal\" -> \\le. math_cues = for each step the "
            "sentence id where it is said. items = up to 3 short notes. Build the formula up: each step may repeat "
            "the previous one with one more term or with numbers substituted, ending in the result."),
        "comparison": "2-3 columns, each a heading and 1-3 short points.",
        "table": (
            "headers + rows of short cell strings; only values from the text. The first column usually names the "
            "row (e.g. \"00\"). row_cues = for each row, the id of the sentence where the narration reads that row "
            "(same length as rows). Keep cells tiny: digits, symbols, 1-3 words.\n"
            "Example for \"[7] Row 00 reads 1 0 0 0. [8] Row 01 reads 0 1 0 0.\": "
            '{"title": "Probability table", "table": {"headers": ["Cards", "00", "01", "10", "11"], '
            '"rows": [["00", "1", "0", "0", "0"], ["01", "0", "1", "0", "0"]], "cue_sentence": 7, "row_cues": [7, 8]}}'),
        "timeline": "items in chronological order; when = the date/year from the text.",
        "number_line": "axis.min/max bracket all values; items = markers (value) or ranges (value..to). to = null for a single marker.",
        "bar_chart": "items = bars; text = bar label; value = the number from the text; unit e.g. \"%\".",
        "plot": (
            "A real, computed plot. curves[].expr = the formula in x using + - * / ^, numbers, pi, and only "
            "sin cos tan exp log sqrt abs sinh cosh tanh arcsin arccos arctan gauss (gauss(x) = exp(-x^2/2)). "
            "Use ONLY the relationship the sentences state (\"falls off as one over r squared\" -> 1/x^2, "
            "\"oscillates\" -> sin(x)). x_min/x_max may be numbers or like \"2*pi\". marks = bounds, thresholds "
            "or special points the sentences mention: hline (y = a bound, x null), vline (x = a value, y null), "
            "point (both x and y). Labels 1-5 words from the sentences. axis labels = the quantities named.\n"
            "Example for \"[12] The quantum correlation is 2 root 2 times cos of the angle. "
            "[13] Classical strategies never exceed 2.\": "
            '{"title": "Quantum beats the classical bound", "axis": {"x_label": "angle", "y_label": "correlation"}, '
            '"plot": {"x_min": 0, "x_max": "pi", "curves": [{"expr": "2*sqrt(2)*cos(x)", "label": "Quantum", '
            '"cue_sentence": 12}], "marks": [{"kind": "hline", "x": null, "y": 2, "label": "Classical bound", '
            '"cue_sentence": 13}]}}'),
        "line_chart": "items = curves: text = curve label, shape = its qualitative trend. This chart is schematic.",
        "cards_grid": "items: head = 1-3 word concept name, text = one short line.",
        "flow": "items = the steps/boxes in order; arrows connect them.",
        "quote_or_definition": "title = the term or source; quote.text = definition or quote (verbatim where possible); attribution may be empty.",
        "title_card": "subtitle = one short line.",
        "recap": "items = the key takeaways stated in these sentences.",
        "bullet_list": "items = the points, in the order they are narrated.",
    }.get(t, "")
    user = f"""Part {part['part']}: "{part['title']}". Template: {t} ({TEMPLATE_HELP.get(t, '')}).
Working title: "{scene.get('title', '')}" (you may improve it).

Sentences of this scene:
{_sentences_block(sents)}

Fill the {t} template. {extra}
Allowed cue_sentence values: {[s['id'] for s in sents]}."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------- heuristic
def shorten(text: str, max_words: int) -> str:
    """Verbatim prefix of a sentence, cut at a clause boundary if possible."""
    t = text.strip().rstrip(".!?")
    ws = t.split()
    if len(ws) <= max_words:
        return t
    cut = " ".join(ws[:max_words])
    m = re.match(r"^(.{20,}?)[,;:—-]\s", cut + " ")
    return (m.group(1) if m else cut).rstrip(",;:") + "…"


def part_title_scene(part: dict) -> dict:
    head = [s for s in part["sentences"] if s["heading"]]
    return {"id": f"p{part['part']}s0", "part": part["part"], "sentences": [head[0]["id"]], "template": "part_title",
            "title": part["title"], "subtitle": f"Part {part['part']}" if part["part"] else "", "tag": "none",
            "source": "auto", "approved": None, "items": []}


def heuristic_part(part: dict, cfg: dict) -> list[dict]:
    sb = cfg["storyboard"]
    scenes = []
    if any(s["heading"] for s in part["sentences"]):
        scenes.append(part_title_scene(part))
    body = [s for s in part["sentences"] if not s["heading"]]
    per = max(1, min(3, sb["max_sentences_per_scene"]))
    for k in range(0, len(body), per):
        grp = body[k:k + per]
        scenes.append(_fallback_scene(part, grp, cfg, len(scenes)))
    return scenes


def _fallback_scene(part: dict, grp: list[dict], cfg: dict, idx: int, title: str | None = None) -> dict:
    # kinetic typography of the narration itself: accurate by construction, and it moves
    return {"id": f"p{part['part']}s{idx}", "part": part["part"], "sentences": [s["id"] for s in grp],
            "template": "spoken", "title": "", "items": [], "tag": "none", "source": "heuristic", "approved": None}


# ---------------------------------------------------------------------------- LLM
def llm_part(part: dict, cfg: dict, llm: LLM, log=print) -> list[dict]:
    sb = cfg["storyboard"]
    body = [s for s in part["sentences"] if not s["heading"]]
    scenes = [part_title_scene(part)] if any(s["heading"] for s in part["sentences"]) else []
    if not body:
        return scenes
    grouping = []
    # long parts overwhelm a small model: group them one window at a time
    for w in range(0, len(body), GROUP_WINDOW):
        ids = [s["id"] for s in body[w:w + GROUP_WINDOW]]
        try:
            grouping += llm.json(grouping_prompt(part, body[w:w + GROUP_WINDOW], sb["max_sentences_per_scene"]),
                                 GROUPING_SCHEMA, log=log,
                                 validate=lambda o, ids=ids: validate_grouping(o["scenes"], ids,
                                                                              sb["max_sentences_per_scene"]))["scenes"]
        except LLMError as e:
            log(f"Part {part['part']}: grouping failed for sentences {ids[0]}-{ids[-1]} ({e}); heuristic grouping")
            per = min(3, sb["max_sentences_per_scene"])
            # no title: the part title repeated on every fallback scene says nothing
            grouping += [{"sentences": ids[k:k + per], "template": "spoken", "title": "", "tag": "none"}
                         for k in range(0, len(ids), per)]
    by_id = {s["id"]: s for s in body}
    for g in grouping:
        sents = [by_id[i] for i in g["sentences"]]
        scene = {"id": f"p{part['part']}s{len(scenes)}", "part": part["part"], "sentences": g["sentences"],
                 "template": g["template"], "title": g["title"], "tag": g.get("tag", "none"),
                 "source": "llm", "approved": None}
        if g["template"] == "spoken":
            scenes.append({**_fallback_scene(part, sents, cfg, 0), "id": scene["id"], "_retry": True})
        else:
            scenes.append(fill_scene(part, scene, sents, cfg, llm, log))
    return scenes


def fill_scene(part: dict, scene: dict, sents: list[dict], cfg: dict, llm: LLM, log=print) -> dict:
    t = scene["template"]
    schema = content_schema(t, cfg["storyboard"]["max_items"])

    contrast = has_contrast(" ".join(s["text"] for s in sents))

    def validate(obj):
        # fix small slips deterministically (missing sides, dangling links...) before judging
        return validate_scene(tidy_diagram({**scene, **obj, "_contrast": contrast}), cfg)

    try:
        obj = llm.json(content_prompt(part, scene, sents), schema, validate=validate, log=log)
        out = flow_to_diagram(tidy_diagram({**scene, **obj, "source": "llm", "_contrast": contrast}))
        out.pop("_contrast", None)
        log(f"  scene {scene['id']} [{out['template']}] '{out['title']}' sentences {scene['sentences']}")
        return out
    except LLMError as e:
        log(f"  scene {scene['id']}: LLM failed for template {t} ({str(e)[:200]}); verbatim fallback")
        fb = _fallback_scene(part, sents, cfg, 0, scene.get("title"))
        fb["id"] = scene["id"]
        fb["notes"] = [f"LLM could not produce a valid {t}; verbatim fallback used"]
        fb["_retry"] = t
        return fb


def build(script: dict, cfg: dict, use_llm: bool = True, log=print, parts: list[int] | None = None,
          existing: dict | None = None) -> dict:
    llm = LLM(cfg) if use_llm else None
    scenes: list[dict] = []
    keep = {s["part"]: [] for s in (existing or {}).get("scenes", [])}
    for s in (existing or {}).get("scenes", []):
        keep[s["part"]].append(s)
    for part in script["parts"]:
        if parts is not None and part["part"] not in parts and part["part"] in keep:
            scenes += keep[part["part"]]
            continue
        log(f"Storyboard: part {part['part']} '{part['title']}' ({len(part['sentences'])} sentences)"
            + (" with LLM" if llm else " (heuristic, no LLM)"))
        scenes += llm_part(part, cfg, llm, log) if llm else heuristic_part(part, cfg)
    if llm and cfg["llm"].get("strong_model"):
        escalate(script, scenes, cfg, llm.with_model(cfg["llm"]["strong_model"]), log)
    for sc in scenes:
        sc.pop("_retry", None)
    _renumber(scenes)
    names = name_words(script)
    scenes = [sentence_case_scene(s, names) for s in scenes]
    return {"version": 1, "scenes": scenes}


GROUP_WINDOW = 12


def weak_scene(scene: dict, sents: list[dict]) -> str | None:
    """The template a second pass should try, or None if the scene is fine as it is.

    Failed scenes retry their own template; text-only scenes whose sentences clearly read out
    a table / formula / number get that visual instead.
    """
    if scene.get("template") == "part_title":
        return None
    hint = scene_hint(sents)
    if scene.get("template") in ("spoken", "bullet_list", "title_card") and hint:
        return hint
    retry = scene.get("_retry")
    if isinstance(retry, str):
        return retry
    return "diagram" if retry else None


def escalate(script: dict, scenes: list[dict], cfg: dict, strong: LLM, log=print) -> None:
    """Stage 3: after the small model has done EVERY scene, the bigger model redoes the weak ones.

    A separate pass (not interleaved) means Ollama swaps models on the GPU once, not per scene.
    """
    parts = {p["part"]: p for p in script["parts"]}
    todo = []
    for i, sc in enumerate(scenes):
        part = parts[sc["part"]]
        sents = [s for s in part["sentences"] if s["id"] in sc["sentences"]]
        if t := weak_scene(sc, sents):
            todo.append((i, part, sents, t))
    if not todo:
        return
    log(f"Second pass: {len(todo)} scene(s) with {strong.model}")
    for i, part, sents, t in todo:
        sc = scenes[i]
        base = {"id": sc["id"], "part": sc["part"], "sentences": sc["sentences"], "template": t,
                "title": sc.get("title") or "", "tag": sc.get("tag", "none"), "source": "llm", "approved": None}
        new = fill_scene(part, base, sents, cfg, strong, log)
        if new["template"] != "spoken":           # big model failed too: keep the first version
            scenes[i] = {**new, "source": "llm-strong"}


# ---------------------------------------------------------------------------- tidying
def tidy_diagram(s: dict) -> dict:
    """Repair the slips a small model makes in diagrams instead of rejecting the visual."""
    if s.get("template") != "diagram":
        return s
    import re as _re
    from .icons import normalise
    s = {**s, "nodes": [dict(n) for n in s.get("nodes") or []], "edges": [dict(e) for e in s.get("edges") or []]}
    sids = s.get("sentences") or []
    seen, nodes = set(), []
    for n in s["nodes"]:
        nid = _re.sub(r"[^a-z0-9]+", "-", str(n.get("id") or n.get("label") or "n").lower()).strip("-") or "n"
        while nid in seen:
            nid += "x"
        seen.add(nid)
        old = n.get("id")
        n["id"] = nid
        n["_old"] = old
        n["icon"] = normalise(n.get("icon"))
        if n.get("cue_sentence") not in sids and sids:
            n["cue_sentence"] = sids[0]
        nodes.append(n)
    remap = {n.pop("_old"): n["id"] for n in nodes}
    by_label = {_re.sub(r"[^a-z0-9]+", "-", n["label"].lower()).strip("-"): n["id"] for n in nodes}
    edges = []
    for e in s["edges"]:
        for k in ("from", "to"):
            v = e.get(k)
            e[k] = remap.get(v) or by_label.get(_re.sub(r"[^a-z0-9]+", "-", str(v).lower()).strip("-")) or v
        if e["from"] in seen and e["to"] in seen and e["from"] != e["to"]:
            if e.get("cue_sentence") not in sids and sids:
                e["cue_sentence"] = sids[-1]
            e.setdefault("style", "arrow")
            e.setdefault("label", "")
            edges.append(e)
    s["nodes"], s["edges"] = nodes, edges
    s["focus"] = [{**f, "node": remap.get(f.get("node"), f.get("node"))} for f in s.get("focus") or []
                  if remap.get(f.get("node"), f.get("node")) in seen]
    if s.get("layout") == "versus" and not s.get("_contrast", True):
        # nothing is being compared: show how the parts connect instead
        deg = {n["id"]: 0 for n in nodes}
        for e in edges:
            deg[e["from"]] += 1
            deg[e["to"]] += 1
        s["layout"] = "hub" if deg and max(deg.values()) >= 2 and len(nodes) >= 3 else "chain"
    if s.get("layout") == "versus" and not ({"A", "B"} <= {n.get("group") for n in nodes}):
        half = (len(nodes) + 1) // 2                    # sides not given: first half vs second half
        for k, n in enumerate(nodes):
            n["group"] = "A" if k < half else "B"
    if s.get("layout") == "versus" and len(nodes) < 2:
        s["layout"] = "chain"
    return s


def flow_to_diagram(s: dict) -> dict:
    """A 'flow' (boxes and arrows) becomes an animated chain diagram; a two-column comparison
    becomes an animated 'versus' diagram (each side: its heading, its points as notes/nodes)."""
    if s.get("template") == "comparison":
        return _comparison_to_versus(s)
    if s.get("template") != "flow" or len(s.get("items") or []) < 2:
        return s
    items = s["items"][:6]
    nodes = [{"id": f"n{k}", "label": " ".join(it["text"].split()[:5]), "icon": "circle-dot",
              "cue_sentence": it["cue_sentence"]} for k, it in enumerate(items)]
    edges = [{"from": f"n{k}", "to": f"n{k + 1}", "label": "", "style": "arrow",
              "cue_sentence": items[k + 1]["cue_sentence"]} for k in range(len(items) - 1)]
    return {**{k: v for k, v in s.items() if k != "items"}, "template": "diagram", "layout": "chain",
            "nodes": nodes, "edges": edges}


CONTRAST = re.compile(r"\b(but|whereas|while|versus|vs|compared|comparison|than|unlike|instead|rather|however|"
                      r"contrast|differ|different|difference|both|either|neither|against|alternative|larger|smaller|"
                      r"more|less|higher|lower)\b", re.I)


def has_contrast(text: str) -> bool:
    """Does the narration actually compare two things? ('versus' layouts need this.)

    "more than five standard deviations" is a size, not a comparison of two things.
    """
    t = re.sub(r"\b(more|less|fewer|greater|higher|lower)\s+than\s+(\S*\d\S*|one|two|three|four|five|six|seven|"
               r"eight|nine|ten|a|an|half|twice)\b", " ", text, flags=re.I)
    return bool(CONTRAST.search(t))


def _comparison_to_versus(s: dict) -> dict:
    cols = s.get("columns") or []
    if len(cols) != 2 or any(len(c.get("heading", "").split()) > 5 or not c.get("heading") for c in cols):
        return s
    nodes = []
    for side, col in zip("AB", cols):
        its = [i for i in col.get("items") or [] if i.get("text")]
        cue = its[0]["cue_sentence"] if its else s["sentences"][0]
        head = {"id": f"{side.lower()}0", "label": col["heading"], "icon": "circle-dot", "group": side, "cue_sentence": cue}
        if its and len(its[0]["text"].split()) <= 8:
            head["note"] = its[0]["text"]
            its = its[1:]
        nodes.append(head)
        for k, it in enumerate(its[:2]):                 # up to 3 nodes per side
            if len(it["text"].split()) <= 5:
                nodes.append({"id": f"{side.lower()}{k + 1}", "label": it["text"], "icon": "circle-dot",
                              "group": side, "cue_sentence": it["cue_sentence"]})
    return {**{k: v for k, v in s.items() if k != "columns"}, "template": "diagram", "layout": "versus",
            "nodes": nodes, "edges": []}


def name_words(script: dict) -> set[str]:
    """Words the narration capitalises in mid-sentence (names, acronyms): keep them capitalised."""
    import re as _re
    out = set()
    for p in script["parts"]:
        for s in p["sentences"]:
            if s.get("heading"):
                continue                     # a Title Cased heading would make every word look like a name
            ws = s["text"].split()
            for k, w in enumerate(ws):
                core = _re.sub(r"'s$", "", w.strip(".,;:!?\"'()[]{}“”"))
                if not core:
                    continue
                if (k > 0 and core[0].isupper() and not ws[k - 1].endswith((".", "!", "?", ":"))) or \
                        (sum(c.isupper() for c in core) >= 2) or any(c.isdigit() for c in core):
                    out.add(core)
    return out


def sentence_case(text: str, names: set[str]) -> str:
    """'Bell Bound vs Local Hidden Variables' -> 'Bell bound vs local hidden variables'."""
    import re as _re

    def fix(piece: str, keep: bool) -> str:
        core = _re.sub(r"'s$", "", piece.strip(".,;:!?\"'()[]{}"))
        if keep or not core or core in names or sum(c.isupper() for c in core) >= 2 or \
                any(c.isdigit() for c in core) or not core[0].isupper():
            return piece
        return piece.replace(core[0], core[0].lower(), 1)

    out = []
    for k, w in enumerate(text.split()):
        if w in names:
            out.append(w)
            continue
        pieces = w.split("-")                     # "Loophole-Free" -> "Loophole-free"
        out.append("-".join(fix(p, k == 0 and j == 0) for j, p in enumerate(pieces)))
    return " ".join(out)


def sentence_case_scene(s: dict, names: set[str]) -> dict:
    s = dict(s)
    for k in ("title", "subtitle", "label"):
        if isinstance(s.get(k), str):
            s[k] = sentence_case(s[k], names)
    for coll, field in (("nodes", "label"), ("edges", "label"), ("items", "text"), ("items", "head")):
        if s.get(coll):
            s[coll] = [{**x, field: sentence_case(x[field], names)} if isinstance(x.get(field), str) else x
                       for x in s[coll]]
    if s.get("columns"):
        s["columns"] = [{**c, "heading": sentence_case(c.get("heading", ""), names),
                         "items": [{**i, "text": sentence_case(i.get("text", ""), names)} for i in c.get("items", [])]}
                        for c in s["columns"]]
    if isinstance(s.get("value"), str):
        import re as _re
        s["value"] = _re.sub(r"(\d+)\s*root\s*(\d+)", r"\1√\2", s["value"])   # "2 root 2" -> "2√2"
    return s


def _renumber(scenes: list[dict]) -> None:
    count: dict[int, int] = {}
    for s in scenes:
        k = count.get(s["part"], 0)
        s["id"] = f"p{s['part']}s{k}"
        count[s["part"]] = k + 1


def regenerate_scene(script: dict, storyboard: dict, scene_id: str, cfg: dict, template: str | None = None,
                     log=print) -> dict:
    llm = LLM(cfg)
    idx = next(i for i, s in enumerate(storyboard["scenes"]) if s["id"] == scene_id)
    scene = dict(storyboard["scenes"][idx])
    if scene["template"] == "part_title":
        return scene
    if template:
        scene["template"] = template
    part = next(p for p in script["parts"] if p["part"] == scene["part"])
    sents = [s for s in part["sentences"] if s["id"] in scene["sentences"]]
    base = {k: scene[k] for k in ("id", "part", "sentences", "template", "title", "tag")}
    new = fill_scene(part, {**base, "approved": None}, sents, cfg, llm, log)
    storyboard["scenes"][idx] = new
    return new


def scene_fingerprint(scene: dict) -> str:
    import hashlib
    content = {k: v for k, v in scene.items() if k not in ("approved", "notes", "source", "id")}
    return hashlib.sha1(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
