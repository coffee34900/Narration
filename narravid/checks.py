"""Stage 5 — accuracy verification of the storyboard (before rendering).

Levels:  error    -> scene is flagged; needs your edit or explicit approval
         warning  -> shown, does not block (e.g. number is in the script but not in this scene)
         advisory -> optional LLM self-check; never treated as proof, never blocks

1 numbers   every number / % / year / sample size / √-expression on screen must be in the script
2 names     proper nouns and gene/probe/symbol identifiers must be in the script
3 math      every LaTeX string must compile in KaTeX (headless, local bundle)
4 coverage  every script sentence belongs to exactly one scene, no gaps / overlaps
5 LLM       optional self-check per scene (advisory)
6 schematic data-like visuals not computed from script numbers get a visible label
"""
from __future__ import annotations

import re

from . import tools
from .schema import validate_scene
from .sentences import all_sentences
from .storyboard import scene_fingerprint
from .textnorm import COMMON_FIRST, fmt_num, identifiers, number_facts, proper_nouns, stem

LATEX_CMD_WORDS = {"alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "kappa",
                   "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "phi", "varphi", "chi", "psi", "omega"}


class ScriptIndex:
    def __init__(self, sentences: list[dict]):
        self.values: dict[int, set[str]] = {}
        self.compounds: dict[int, set[str]] = {}
        self.all_values: set[str] = set()
        self.all_compounds: set[str] = set()
        self.words: set[str] = set()
        self.idents: set[str] = set()
        self.text = {s["id"]: s["text"] for s in sentences}
        for s in sentences:
            facts = number_facts(s["text"])
            self.values[s["id"]] = {f["value"] for f in facts}
            self.compounds[s["id"]] = {f["compound"] for f in facts if f["compound"]}
            self.all_values |= self.values[s["id"]]
            self.all_compounds |= self.compounds[s["id"]]
            for w in s["text"].split():
                core = re.sub(r"'s$", "", w.strip(".,;:!?\"'()[]{}“”‘’")).lower()
                if core:
                    self.words.add(core)
                    self.words.update(p for p in re.split(r"[-/]", core) if p)
            self.idents |= {i.lower() for i in identifiers(s["text"])}
        self.stems = {stem(w) for w in self.words}

    def scene_sets(self, sids: list[int]) -> tuple[set[str], set[str]]:
        v, c = set(), set()
        for s in sids:
            v |= self.values.get(s, set())
            c |= self.compounds.get(s, set())
        return v, c


APPROX_WORDS = {"about", "roughly", "approximately", "around", "nearly", "almost", "some", "circa", "ca", "approx",
                "~", "≈", "~", "estimated", "close"}
COMPARE_WORDS = {"more", "less", "fewer", "over", "under", "above", "below", "least", "most", "up", "exceeds",
                 "exceeded", "exceed", "greater", "higher", "lower", "smaller", "larger", "beyond", "than", "within",
                 ">", "<", "≥", "≤", "⩾", "⩽", "+"}
COMPARE_SYMBOLS = (">", "<", "≥", "≤", "⩾", "⩽")


def _ctx_words(ws: list[str]) -> set[str]:
    return {w.strip(".,;:!?\"'()[]").lower() for w in ws}


def number_qualifiers(text: str) -> dict[str, list[set[str]]]:
    """For each number in a sentence, the qualifier kinds around it: {'245': [{'approx'}], ...}."""
    from .textnorm import canon_tokens
    words = text.split()
    out: dict[str, list[set[str]]] = {}
    for t in canon_tokens(words):
        if not re.fullmatch(r"\d+(?:\.\d+)?", t.text):
            continue
        before = _ctx_words(words[max(0, t.start - 3):t.start])
        after = _ctx_words(words[t.end + 1:t.end + 4])
        kinds = set()
        if before & APPROX_WORDS:
            kinds.add("approx")
        if (before | after) & {"more", "less", "fewer", "over", "under", "above", "below", "least", "most",
                               "exceeds", "greater", "higher", "lower", "beyond"} or ("up" in before and "to" in before):
            kinds.add("compare")
        out.setdefault(t.text, []).append(kinds)
    return out


def _screen_has(kind: str, context: str) -> bool:
    ws = _ctx_words(re.split(r"\s+", context))
    if kind == "approx":
        return bool(ws & APPROX_WORDS) or any(c in context for c in "~≈")
    return bool(ws & COMPARE_WORDS) or any(c in context for c in COMPARE_SYMBOLS)


def screen_texts(scene: dict) -> list[tuple[str, str]]:
    """All on-screen strings with a field path, e.g. ("items[2].text", "...")."""
    out = []

    def add(path, v):
        if isinstance(v, str) and v.strip():
            out.append((path, v))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((path, fmt_num(v)))

    for k in ("title", "subtitle", "value", "label", "unit"):
        add(k, scene.get(k))
    for i, it in enumerate(scene.get("items") or []):
        for f in ("text", "head", "when", "value", "to"):
            add(f"items[{i}].{f}", it.get(f))
    for c, col in enumerate(scene.get("columns") or []):
        add(f"columns[{c}].heading", col.get("heading"))
        for i, it in enumerate(col.get("items") or []):
            add(f"columns[{c}].items[{i}].text", it.get("text"))
    tb = scene.get("table") or {}
    for i, h in enumerate(tb.get("headers") or []):
        add(f"table.headers[{i}]", h)
    for r, row in enumerate(tb.get("rows") or []):
        for c, cell in enumerate(row):
            add(f"table.rows[{r}][{c}]", cell)
    q = scene.get("quote") or {}
    add("quote.text", q.get("text"))
    add("quote.attribution", q.get("attribution"))
    for i, nd in enumerate(scene.get("nodes") or []):
        add(f"nodes[{i}].label", nd.get("label"))
        add(f"nodes[{i}].note", nd.get("note"))
    for i, e in enumerate(scene.get("edges") or []):
        add(f"edges[{i}].label", e.get("label"))
    ax = scene.get("axis") or {}
    for f in ("unit", "x_label", "y_label"):
        add(f"axis.{f}", ax.get(f))
    if scene.get("template") == "number_line":
        for f in ("min", "max"):
            add(f"axis.{f}", ax.get(f))
    return out


def latex_numbers(tex: str) -> list[str]:
    t = re.sub(r"\\[A-Za-z]+", " ", tex)
    return [fmt_num(float(m)) for m in re.findall(r"\d+(?:\.\d+)?", t)]


def latex_symbols(tex: str) -> list[str]:
    return [m for m in re.findall(r"\\([A-Za-z]+)", tex) if m.lower() in LATEX_CMD_WORDS]


def is_schematic(scene: dict, idx: ScriptIndex) -> bool:
    t = scene.get("template")
    if t in ("line_chart", "diagram"):        # conceptual drawings are illustrative by nature
        return True
    if t in ("bar_chart", "number_line"):
        for it in scene.get("items") or []:
            for f in ("value", "to"):
                v = it.get(f)
                if v is None or v == "":
                    continue
                try:
                    if fmt_num(float(v)) not in idx.all_values:
                        return True
                except (TypeError, ValueError):
                    return True
    return False


def check_scene(scene: dict, idx: ScriptIndex, cfg: dict, katex_results: dict[str, dict]) -> list[dict]:
    res: list[dict] = []
    sids = scene.get("sentences") or []
    sv, sc = idx.scene_sets(sids)

    def add(level, check, field, msg):
        res.append({"level": level, "check": check, "field": field, "message": msg})

    for e in validate_scene(scene, cfg):
        add("error", "rules", "", e)

    quals: dict[str, list[set[str]]] = {}
    for sid in sids:
        for v, ks in number_qualifiers(idx.text.get(sid, "")).items():
            quals.setdefault(v, []).extend(ks)

    # 1 numbers ----------------------------------------------------------
    for path, text in screen_texts(scene):
        # what the viewer reads as bound to this number: a key_number's value is read with its label
        # (not the title — "Efficiency below target / 0.08 / detection efficiency" still reads as "efficiency = 0.08")
        context = text
        if path in ("value", "label"):
            context = " ".join(str(scene.get(k, "")) for k in ("value", "label"))
        elif path.startswith("items[") and path.endswith((".value", ".to")):
            it = scene["items"][int(path[6:path.index("]")])]
            context = " ".join(str(it.get(k, "")) for k in ("text", "value", "to", "head"))
        for f in number_facts(text):
            v, c = f["value"], f["compound"]
            occ = quals.get(v)
            if occ and all(occ):                       # every mention in the scene's sentences is qualified
                for kind in ("compare", "approx"):
                    if all(kind in k for k in occ) and not _screen_has(kind, context):
                        add("error" if kind == "compare" else "warning", "qualifier", path,
                            f"script qualifies {v} ({'a comparison, e.g. below / more than' if kind == 'compare' else 'approximate, e.g. about / roughly'}) "
                            f"but the screen shows it bare: '{context.strip()[:60]}'")
            if c:
                if c in sc:
                    continue
                if c in idx.all_compounds:
                    add("warning", "numbers", path, f"'{f['src']}' is in the script but not in this scene's sentences")
                elif v in sv or v in idx.all_values:
                    add("error", "numbers", path, f"'{f['src']}' — the script has {v} but not as '{c}'")
                else:
                    add("error", "numbers", path, f"'{f['src']}' not found in the script")
            else:
                if v in sv:
                    continue
                if v in idx.all_values:
                    add("warning", "numbers", path, f"'{f['src']}' is in the script but not in this scene's sentences")
                else:
                    add("error", "numbers", path, f"'{f['src']}' not found in the script")
        # 2 names --------------------------------------------------------
        for ident in identifiers(text):
            if ident.lower() not in idx.idents and ident.lower() not in idx.words:
                add("error", "names", path, f"identifier '{ident}' not found in the script")
        inner = set(proper_nouns(text))
        for name in proper_nouns(text, with_first=True):
            if name.lower() in idx.words or stem(name) in idx.stems:
                continue
            if name not in inner and name.lower() in COMMON_FIRST:
                continue                        # an ordinary sentence-case opening word
            add("error", "names", path, f"name '{name}' not found in the script")

    # 3 math ----------------------------------------------------------------
    trivial = {fmt_num(n) for n in cfg["checks"]["math_trivial_numbers"]}
    for i, tex in enumerate(scene.get("math") or []):
        r = katex_results.get(tex, {"ok": True})
        if not r["ok"]:
            add("error", "math", f"math[{i}]", f"KaTeX cannot render: {r['error']}")
        for n in latex_numbers(tex):
            if n in trivial or n in sv:
                continue
            level = "warning" if n in idx.all_values else "error"
            add(level, "numbers", f"math[{i}]", f"number {n} in formula " +
                ("is not in this scene's sentences" if level == "warning" else "not found in the script"))
        for sym in latex_symbols(tex):
            if sym.lower() not in idx.words:
                add("warning", "names", f"math[{i}]", f"symbol \\{sym} is never named ('{sym}') in the script")

    # quotes must be verbatim when attributed ------------------------------
    q = scene.get("quote") or {}
    if q.get("text") and (q.get("attribution") or "").strip():
        hay = _norm(" ".join(idx.text.get(s, "") for s in sids))
        if _norm(q["text"]) not in hay:
            add("error", "quote", "quote.text", "attributed quote is not verbatim from the scene's sentences")

    # 6 schematic -----------------------------------------------------------
    if is_schematic(scene, idx):
        add("info", "schematic", "", "data-like visual not fully computed from script numbers — "
            "rendered with a visible 'schematic / illustrative' label")
    return res


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9%.]+", " ", s.lower()).strip()


def coverage(script: dict, storyboard: dict) -> list[dict]:
    ids = [s["id"] for s in all_sentences(script)]
    seen: dict[int, list[str]] = {}
    for sc in storyboard["scenes"]:
        for s in sc["sentences"]:
            seen.setdefault(s, []).append(sc["id"])
    out = []
    gaps = [i for i in ids if i not in seen]
    if gaps:
        out.append({"level": "error", "check": "coverage", "message": f"sentences in no scene: {gaps}"})
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if dups:
        out.append({"level": "error", "check": "coverage", "message": f"sentences in several scenes: {dups}"})
    unknown = [k for k in seen if k not in ids]
    if unknown:
        out.append({"level": "error", "check": "coverage", "message": f"scenes reference unknown sentences: {unknown}"})
    order = [s for sc in storyboard["scenes"] for s in sc["sentences"]]
    if order != sorted(order):
        out.append({"level": "error", "check": "coverage", "message": "scenes are not in narration order"})
    return out


def compile_math(storyboard: dict) -> dict[str, dict]:
    texs = sorted({t for s in storyboard["scenes"] for t in (s.get("math") or [])})
    if not texs:
        return {}
    res = tools.node_script("katex_check.mjs", {"items": texs})["results"]
    return dict(zip(texs, res))


SELF_CHECK_SCHEMA = {"type": "object", "properties": {"supported": {"type": "boolean"},
                                                      "problems": {"type": "array", "items": {"type": "string"}}},
                     "required": ["supported", "problems"]}


def llm_self_check(scene: dict, sentences: list[dict], llm) -> dict:
    shown = [f"{p}: {t}" for p, t in screen_texts(scene)] + [f"math: {m}" for m in scene.get("math") or []]
    msgs = [{"role": "system", "content": "You are a strict fact-checker. Answer JSON only."},
            {"role": "user", "content": "Sentences:\n" + "\n".join(f"[{s['id']}] {s['text']}" for s in sentences) +
             "\n\nOn-screen text:\n" + "\n".join(shown) +
             "\n\nIs every on-screen claim directly supported by these sentences? "
             'Answer {"supported": bool, "problems": [short descriptions of unsupported claims]}.'}]
    return llm.json(msgs, SELF_CHECK_SCHEMA)


def run(script: dict, storyboard: dict, cfg: dict, use_llm: bool = False, log=print) -> dict:
    sents = all_sentences(script)
    idx = ScriptIndex(sents)
    katex = compile_math(storyboard)
    llm = None
    if use_llm:
        from .llm import LLM
        llm = LLM(cfg)
    by_id = {s["id"]: s for s in sents}
    scenes = {}
    for sc in storyboard["scenes"]:
        results = check_scene(sc, idx, cfg, katex)
        if llm and sc["template"] != "part_title":
            try:
                r = llm_self_check(sc, [by_id[i] for i in sc["sentences"] if i in by_id], llm)
                if not r["supported"] or r["problems"]:
                    for p in r["problems"] or ["model says not fully supported"]:
                        results.append({"level": "advisory", "check": "llm", "field": "", "message": p})
            except Exception as e:  # noqa: BLE001
                results.append({"level": "advisory", "check": "llm", "field": "", "message": f"self-check failed: {e}"})
        errors = [r for r in results if r["level"] == "error"]
        fp = scene_fingerprint(sc)
        approved = sc.get("approved") == fp
        status = "ok" if not errors else ("approved" if approved else "flagged")
        scenes[sc["id"]] = {"status": status, "fingerprint": fp, "results": results,
                            "schematic": is_schematic(sc, idx)}
        if errors and not approved:
            log(f"Check: scene {sc['id']} flagged: " + "; ".join(e["message"] for e in errors[:3]))
    cov = coverage(script, storyboard)
    flagged = [k for k, v in scenes.items() if v["status"] == "flagged"]
    return {"scenes": scenes, "coverage": cov, "flagged": flagged,
            "ok": not flagged and not [c for c in cov if c["level"] == "error"]}
