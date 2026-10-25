"""Stage 6 — deterministic HyperFrames composition from storyboard + timestamps.

Python + Jinja2 only; the LLM is not involved. One self-contained HyperFrames project
per part (composition/part_XX/) plus a full-length preview (composition/preview/).
Times are frame-quantised so chunk renders concatenate with no drift.
"""
from __future__ import annotations

import copy
import math
import shutil
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from . import design, tools
from .checks import ScriptIndex, compile_math, is_schematic
from .sentences import all_sentences
from .textnorm import canon_text, fmt_num

TPL_DIR = Path(__file__).parent / "templates"
STOP = set("a an the of to in on for and or with by from as at is are was were be been that this these those it its "
           "into than then over under via per not no".split())


def _env() -> Environment:
    # Lenient on purpose: an optional field missing from a scene renders as empty instead of
    # aborting the whole video. tests/test_templates_render.py renders every template.
    env = Environment(loader=FileSystemLoader(TPL_DIR), autoescape=True, undefined=ChainableUndefined,
                      trim_blocks=True, lstrip_blocks=True)
    return env


# ------------------------------------------------------------------ timing
def q(t: float, fps: int) -> float:
    """Quantise to a frame boundary."""
    return round(round(t * fps) / fps, 6)


def scene_windows(storyboard: dict, sentences: list[dict], duration: float, fps: int) -> list[dict]:
    """Each scene runs from its first sentence's start to the next scene's start."""
    sid_start = {s["id"]: s["start"] for s in sentences}
    sc = storyboard["scenes"]
    end_total = q(duration, fps)
    n = len(sc)
    gap = 15 / fps                            # shortest scene: half a second
    if n and n * gap > end_total:
        gap = max(1 / fps, end_total / n)
    starts = [min(max(0.0, q(sid_start[s["sentences"][0]], fps)), end_total) for s in sc]
    for k in range(1, n):                     # strictly increasing (no empty clips) ...
        starts[k] = max(starts[k], starts[k - 1] + gap)
    for k in range(n - 1, -1, -1):            # ... and every scene still starts before the audio ends
        starts[k] = min(starts[k], end_total - (n - k) * gap)
        if k + 1 < n:
            starts[k] = min(starts[k], starts[k + 1] - gap)
    starts = [q(max(0.0, t), fps) for t in starts]
    out = []
    for k, s in enumerate(sc):
        end = starts[k + 1] if k + 1 < len(sc) else end_total
        out.append({"id": s["id"], "start": starts[k], "end": max(end, starts[k] + 1 / fps),
                    "sentence_start": sid_start[s["sentences"][0]]})
    return out


def cue_time(text: str, cue_sid: int, words_by_sid: dict[int, list[dict]], fallback: float) -> float:
    """Reveal an item when the narration reaches its first content word in the cue sentence."""
    ws = words_by_sid.get(cue_sid) or []
    if not ws:
        return fallback
    targets = [t for t in canon_text(text) if t not in STOP and len(t) > 2]
    for w in ws:
        toks = canon_text(w["text"])
        if any(t in targets or (len(t) > 4 and any(x.startswith(t[:5]) for x in targets)) for t in toks):
            return max(ws[0]["start"], w["start"] - 0.25)
    return ws[0]["start"]


SCENE_DEFAULTS = {"title": "", "subtitle": "", "items": [], "columns": [], "table": None, "value": "", "label": "",
                  "math": [], "math_cues": [], "quote": None, "axis": None, "unit": "", "tag": "none",
                  "layout": "chain", "nodes": [], "edges": [], "focus": []}


def with_defaults(scene: dict) -> dict:
    """Every optional field present (templates are strict), so any valid scene renders.

    Scenes come from the LLM, from hand edits or from the automatic plain fallback, and each
    fills a different subset of fields; a missing one must never crash the video.
    """
    for k, v in SCENE_DEFAULTS.items():
        if scene.get(k) is None and v is not None:
            scene[k] = copy.deepcopy(v)
        else:
            scene.setdefault(k, v)
    for it in scene["items"]:
        it.setdefault("text", "")
    for c in scene["columns"]:
        c.setdefault("heading", "")
        c.setdefault("items", [])
    if scene["table"] is not None:
        scene["table"].setdefault("headers", [])
        scene["table"].setdefault("rows", [])
    if scene["axis"] is not None or scene.get("template") in ("line_chart", "number_line"):
        scene["axis"] = {"x_label": "", "y_label": "", "unit": "", **(scene["axis"] or {})}
    if scene["quote"] is not None:
        scene["quote"] = {"text": "", "attribution": "", **scene["quote"]}
    return scene


def assign_reveals(scene: dict, win: dict, words_by_sid: dict, offset: float) -> dict:
    """Attach composition-local reveal times (_t*) to a copy of the scene."""
    m = design.MOTION
    s = with_defaults(copy.deepcopy(scene))
    start, end = win["start"] - offset, win["end"] - offset
    latest = max(start, end - m["min_visible"])
    t0 = round(start + 0.15, 3)
    s["_t0"] = t0

    def clamp(t):
        return round(min(max(t - offset, t0 + 0.3), latest), 3)

    def seq(items, key_text=lambda it: it.get("text", "")):
        prev = t0
        for k, it in enumerate(items):
            t = clamp(cue_time(key_text(it), it.get("cue_sentence"), words_by_sid,
                               (words_by_sid.get(it.get("cue_sentence")) or [{"start": win["start"]}])[0]["start"]))
            t = min(max(t, prev + m["min_item_gap"]), latest) if k else max(t, t0 + 0.3)
            it["_t"] = round(t, 3)
            prev = it["_t"]

    seq(s.get("items") or [], lambda it: f"{it.get('head', '')} {it.get('text', '')} {it.get('when', '')}")
    for c in s.get("columns") or []:
        seq(c.get("items") or [])
        c["_t"] = round(max(t0 + 0.3, (c["items"][0]["_t"] - 0.3) if c.get("items") else t0 + 0.3), 3)
    if s.get("table"):
        cs = s["table"].get("cue_sentence") or s["sentences"][0]
        s["table"]["_t"] = clamp((words_by_sid.get(cs) or [{"start": win["start"]}])[0]["start"])
    cues = s.get("math_cues") or []
    tm, prev = [], t0
    for k, _ in enumerate(s.get("math") or []):
        if k < len(cues) and cues[k] in s["sentences"]:
            t = clamp((words_by_sid.get(cues[k]) or [{"start": win["start"]}])[0]["start"])
        else:
            t = t0 + 0.3 + k * 1.2
        t = min(max(t, prev + (m["min_item_gap"] if k else 0)), latest)
        tm.append(round(t, 3))
        prev = t
    s["_tm"] = tm
    s["_tv"] = clamp(cue_time(s.get("value", ""), s["sentences"][0], words_by_sid, win["start"])) \
        if s.get("value") else t0 + 0.3
    s["_count"] = count_parts(s.get("value") or "")
    if s["template"] == "spoken":
        s["_spoken"] = spoken_lines(s, words_by_sid, offset, start, end)
    if s["template"] == "diagram" and s.get("nodes"):
        from . import diagram

        def t_of(cue, text):
            fallback = (words_by_sid.get(cue) or [{"start": win["start"]}])[0]["start"]
            return clamp(cue_time(text, cue, words_by_sid, fallback))

        # space out simultaneous reveals so each node gets its own moment
        s["_diagram_html"] = diagram.render(s, _spaced(t_of, t0 + 0.3, m["min_item_gap"], latest), end - 0.2)
    return s


def spoken_lines(s: dict, words_by_sid: dict, offset: float, start: float, end: float) -> list[dict]:
    """One block per sentence: shown while it is spoken, each word lighting up on its own timestamp.

    Numbers, names and acronyms are marked as key words (accent colour).
    """
    import re
    blocks = []
    for sid in s["sentences"]:
        ws = words_by_sid.get(sid) or []
        if not ws:
            continue
        text = " ".join(w["text"] for w in ws)
        size = 54 if len(text) <= 150 else 46 if len(text) <= 260 else 38
        words = []
        for k, w in enumerate(ws):
            core = w["text"].strip(".,;:!?\"'()")
            key = bool(re.search(r"\d", core)) or (k > 0 and core[:1].isupper()) or sum(c.isupper() for c in core) >= 2
            words.append({"text": w["text"], "t": round(max(start, w["start"] - offset), 3), "key": key})
        blocks.append({"t_in": round(max(start + 0.05, ws[0]["start"] - offset - 0.25), 3), "size": size, "words": words})
    for k, b in enumerate(blocks):
        nxt = blocks[k + 1]["t_in"] if k + 1 < len(blocks) else None
        b["t_out"] = round(nxt - 0.35, 3) if nxt is not None and nxt - 0.35 > b["t_in"] + 0.5 else None
    return blocks


def _spaced(t_of, first: float, gap: float, latest: float):
    """Wrap t_of so successive calls never land closer than `gap` seconds (nodes, then edges)."""
    seen: list[float] = []

    def f(cue, text):
        t = max(first, t_of(cue, text))
        while any(abs(t - x) < gap * 0.75 for x in seen) and t < latest:
            t = min(latest, t + gap * 0.5)
        seen.append(t)
        return round(t, 3)
    return f


def count_parts(value: str) -> dict | None:
    """'75%' -> prefix '', number 75, suffix '%' so the number can count up to exactly what is said."""
    import re
    m = re.fullmatch(r"\s*([^\d\-.]*?)(-?\d[\d,]*(?:\.\d+)?|-?\.\d+)(.*?)\s*", value or "")
    if not m:
        return None
    num = m.group(2)
    dec = len(num.split(".")[1]) if "." in num else 0
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    return {"prefix": m.group(1), "text": num, "value": v, "dec": dec, "sep": "," in num, "suffix": m.group(3)}


# ------------------------------------------------------------------ chart geometry
def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def geometry(s: dict) -> dict:
    t = s["template"]
    items = s.get("items") or []
    if t == "timeline":
        n = max(1, len(items))
        span = 1680 - 250
        return {"x": [round(span * (k / (n - 1)) if n > 1 else span / 2) for k in range(n)]}
    if t == "bar_chart":
        vals = [abs(_num(it.get("value"))) for it in items]
        mx = max(vals) if vals and max(vals) > 0 else 1.0
        unit = s.get("unit") or ""
        track = 900
        bars = []
        for k, it in enumerate(items):
            v = _num(it.get("value"))
            label = fmt_num(v) + (unit if unit in ("%", "×", "x") else (" " + unit if unit else ""))
            bars.append({"label": it.get("text", ""), "w": max(6, round(track * abs(v) / mx)), "vlabel": label,
                         "t": it["_t"], "color": "measured" if s.get("tag") in (None, "none", "measured") else s["tag"]})
        return {"bars": bars, "track": track}
    if t == "number_line":
        ax = s.get("axis") or {}
        lo, hi = _num(ax.get("min", 0)), _num(ax.get("max", 1))
        if hi <= lo:
            hi = lo + 1
        x0, x1, y = 120, 1560, 300
        X = lambda v: round(x0 + (x1 - x0) * (min(max(v, lo), hi) - lo) / (hi - lo), 1)  # noqa: E731
        unit = ax.get("unit") or ""
        ticks = [{"x": X(lo), "label": fmt_num(lo) + (unit if unit == "%" else "")},
                 {"x": X(hi), "label": fmt_num(hi) + (unit if unit == "%" else "")}]
        marks = []
        for k, it in enumerate(items):
            v = _num(it.get("value"))
            to = it.get("to")
            up = k % 2 == 0
            color = design.SERIES[k % 4]
            if to is not None and to != "":
                a, b = X(v), X(_num(to))
                a, b = min(a, b), max(a, b)
                marks.append({"range": True, "x": a, "w": max(8, b - a), "lx": (a + b) / 2, "t": it["_t"],
                              "ly": y - 150 if up else y + 80, "by": y - 26 if up else y + 26,
                              "bd": -26 if up else 26, "color": color, "text": it.get("text", ""),
                              "vlabel": f"{fmt_num(v)}–{fmt_num(_num(to))}{unit if unit == '%' else ''}"})
            else:
                marks.append({"range": False, "x": X(v), "lx": X(v), "t": it["_t"], "ly": y - 140 if up else y + 80,
                              "color": color, "text": it.get("text", ""),
                              "vlabel": fmt_num(v) + (unit if unit == "%" else "")})
        for mk in marks:          # keep 400 px-wide labels inside the content box
            mk["lx"] = min(max(mk["lx"], 200), 1680 - 200)
        return {"x0": x0, "x1": x1, "y": y, "ticks": ticks, "marks": marks}
    if t == "line_chart":
        curves = []
        for k, it in enumerate(items):
            pts = []
            for i in range(61):
                x = i / 60
                y = _shape(it.get("shape", "rising"), x) * (0.92 - 0.06 * k) + 0.03 * k
                pts.append(f"{10 + 1090 * x:.1f},{455 - 430 * y:.1f}")
            curves.append({"d": "M" + " L".join(pts), "color": design.SERIES[k % 4], "label": it.get("text", ""),
                           "t": it["_t"]})
        return {"curves": curves}
    return {}


def _shape(name: str, x: float) -> float:
    return {
        "rising": 0.1 + 0.8 * x, "linear": 0.1 + 0.8 * x, "falling": 0.9 - 0.8 * x,
        "peak": 0.1 + 0.8 * math.exp(-((x - 0.5) / 0.17) ** 2),
        "saturating": 0.1 + 0.8 * (1 - math.exp(-4.5 * x)) / (1 - math.exp(-4.5)),
        "oscillating": 0.5 + 0.32 * math.sin(4 * math.pi * x), "flat": 0.5,
        "u_shaped": 0.1 + 0.8 * (2 * x - 1) ** 2,
    }.get(name, 0.1 + 0.8 * x)


# ------------------------------------------------------------------ html
def render_scene(env: Environment, s: dict, math_html: list[str], first_part_title: bool) -> str:
    tag = s.get("tag") or "none"
    cls = f"c-{tag}"
    t = s["template"]
    if t == "recap" and not s.get("title"):
        s["title"] = "Recap"
    macros = env.get_template("scenes.j2").make_module({"series": design.SERIES})
    if t == "part_title":
        legend = list(design.ROLE_LABELS.items()) if first_part_title else None
        return str(macros.part_title(s, cls, legend))
    if t == "title_card":
        return str(macros.title_card(s, cls))
    if t in ("bullet_list", "recap"):
        return str(macros.bullet_list(s, cls, t == "recap"))
    if t == "diagram":
        return str(macros.diagram(s, cls))
    if t == "spoken":
        return str(macros.spoken(s, cls))
    if t == "equation":
        return str(macros.equation(s, cls, math_html))
    if t in ("timeline", "number_line", "bar_chart", "line_chart"):
        return str(getattr(macros, t)(s, cls, geometry(s)))
    return str(getattr(macros, t)(s, cls))


def copy_assets(dst: Path) -> None:
    rd = tools.renderer_dir() / "node_modules"
    a = dst / "assets"
    (a / "fonts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(rd / "gsap" / "dist" / "gsap.min.js", a / "gsap.min.js")
    shutil.copy2(rd / "gsap" / "dist" / "MotionPathPlugin.min.js", a / "MotionPathPlugin.min.js")
    for w in (400, 500, 600, 700):
        shutil.copy2(rd / "@fontsource" / "inter" / "files" / f"inter-latin-{w}-normal.woff2", a / "fonts" / f"inter-{w}.woff2")
    kd = a / "katex"
    if not kd.exists():
        (kd / "fonts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(rd / "katex" / "dist" / "katex.min.css", kd / "katex.min.css")
        for f in (rd / "katex" / "dist" / "fonts").glob("*.woff2"):
            shutil.copy2(f, kd / "fonts" / f.name)


def build(project, cfg: dict, log=print) -> dict:
    fps = int(cfg["render"]["fps"])
    script = project.read_json("script.json")
    sentences = project.read_json("sentences.json")
    words = project.read_json("words.json")
    storyboard = project.read_json("storyboard.json")
    final_wav = project.p("audio/final.wav")
    duration = tools.ffprobe_duration(final_wav)
    idx = ScriptIndex(all_sentences(script))
    katex = compile_math(storyboard)
    bad = [t for t, r in katex.items() if not r["ok"]]
    if bad:
        raise RuntimeError(f"LaTeX that KaTeX cannot render: {bad[:3]} — fix it in the storyboard editor")

    words_by_sid: dict[int, list[dict]] = {}
    for w in words:
        words_by_sid.setdefault(w["sid"], []).append(w)
    wins = scene_windows(storyboard, sentences, duration, fps)
    win_by = {w["id"]: w for w in wins}
    env = _env()
    css = (TPL_DIR / "design.css").read_text(encoding="utf-8")
    parts = [p["part"] for p in script["parts"]]
    part_title = {p["part"]: p["title"] for p in script["parts"]}
    for sc in storyboard["scenes"]:               # the header uses the (sentence-cased) part card title
        if sc["template"] == "part_title" and sc.get("title"):
            part_title[sc["part"]] = sc["title"]

    # Chunks follow the scenes in narration order: a new chunk starts whenever the part changes.
    # Built from the (strictly increasing) scene windows, so chunks can never overlap or run
    # backwards, whatever the part numbers are.
    # Long parts are split further (at scene starts) so one headless Chrome page never has to hold
    # minutes of animated diagrams — that is what crashed long renders ("Target closed").
    max_len = float(cfg["render"].get("chunk_max_seconds", 90))
    runs: list[list[dict]] = []
    for sc in storyboard["scenes"]:
        w = win_by[sc["id"]]
        if runs and runs[-1][0]["part"] == sc["part"] and w["end"] - win_by[runs[-1][0]["id"]]["start"] <= max_len:
            runs[-1].append(sc)
        else:
            runs.append([sc])
    end_total = q(duration, fps)
    bounds = []
    for k, run in enumerate(runs):
        a = 0.0 if k == 0 else q(win_by[run[0]["id"]]["start"], fps)
        b = q(win_by[runs[k + 1][0]["id"]]["start"], fps) if k + 1 < len(runs) else end_total
        if bounds and b - a < 1 / fps:          # degenerate run: fold into the previous chunk
            prev = bounds.pop()
            bounds.append((prev[0], prev[1] + run, prev[2], b))
            continue
        bounds.append((k, run, a, b))
    if not bounds or any(b - a < 1 / fps for _, _, a, b in bounds):
        raise RuntimeError(f"internal timing error: chunk bounds {[(a, b) for _, _, a, b in bounds]}")

    out_dir = project.p("composition")
    timing = {"fps": fps, "duration": duration, "chunks": [], "scenes": []}
    first_pt = next((s["id"] for s in storyboard["scenes"] if s["template"] == "part_title"), None)

    def scene_block(sc, offset):
        win = win_by[sc["id"]]
        s = assign_reveals(sc, win, words_by_sid, offset)
        mh = [katex[m]["html"] for m in s.get("math") or []]
        html = render_scene(env, s, mh, sc["id"] == first_pt)
        tag = s.get("tag") or "none"
        return {"id": sc["id"], "start": round(win["start"] - offset, 6),
                "duration": round(win["end"] - win["start"], 6), "html": html,
                "tag": tag if sc["template"] != "part_title" else "none",
                "tag_label": design.ROLE_LABELS.get(tag, ""), "schematic": is_schematic(sc, idx),
                "quiet_schematic": sc["template"] == "diagram"}

    def headers_for(scenes_in, offset, chunk_end):
        hs = []
        by_part: dict[int, list[dict]] = {}
        for sc in scenes_in:
            by_part.setdefault(sc["part"], []).append(sc)
        for p, scs in by_part.items():
            body = [x for x in scs if x["template"] != "part_title"]
            if not body:
                continue
            a = win_by[body[0]["id"]]["start"] - offset
            b = win_by[body[-1]["id"]]["end"] - offset
            cont = a < 0                        # part began in an earlier chunk: header already showing
            a = max(a, 0.0)
            hs.append({"id": f"hdr_{p}", "start": round(a, 6), "duration": round(min(b, chunk_end) - a, 6),
                       "kicker": f"Part {p}" if p else "Intro", "title": part_title[p], "cont": cont})
        return hs

    tpl = env.get_template("composition.html.j2")
    motion = design.MOTION

    def write(comp_dir: Path, comp_id: str, scenes_in: list[dict], offset: float, dur: float, audio_from: float):
        comp_dir.mkdir(parents=True, exist_ok=True)
        copy_assets(comp_dir)
        tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{audio_from:.6f}",
                   "-t", f"{dur:.6f}", "-i", final_wav, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le",
                   comp_dir / "assets" / "narration.wav"])
        blocks = [scene_block(sc, offset) for sc in scenes_in]
        html = tpl.render(comp_id=comp_id, duration=round(dur, 6), fps=fps, scenes=blocks,
                          headers=headers_for(scenes_in, offset, dur), css=css, css_vars=design.css_vars(),
                          motion=motion)
        (comp_dir / "index.html").write_text(html, encoding="utf-8")
        return blocks

    for n, (_, scs, a, b) in enumerate(bounds):
        p = scs[0]["part"]
        cid = f"part_{n + 1:02d}"
        blocks = write(out_dir / cid, cid, scs, a, b - a, a)
        frames = round((b - a) * fps)
        timing["chunks"].append({"id": cid, "part": p, "start": a, "end": b, "frames": frames})
        for bl in blocks:
            w = win_by[bl["id"]]
            timing["scenes"].append({"id": bl["id"], "chunk": cid, "start": w["start"], "end": w["end"],
                                     "sentence_start": w["sentence_start"], "local_start": bl["start"]})
        log(f"Composed {cid}: {len(scs)} scenes, {b - a:.1f}s ({frames} frames)")
    # remove chunk folders of parts that no longer exist
    keep = {c["id"] for c in timing["chunks"]} | {"preview"}
    for d in out_dir.iterdir():
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
    write(out_dir / "preview", "preview", storyboard["scenes"], 0.0, q(duration, fps), 0.0)
    timing["total_frames"] = sum(c["frames"] for c in timing["chunks"])
    project.write_json("timing.json", timing)
    return timing
