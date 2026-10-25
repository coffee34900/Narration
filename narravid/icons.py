"""Curated Lucide icon vocabulary for schematic diagrams (lucide-static, ISC licence, bundled locally).

The LLM may only choose names from ICONS (enforced by the JSON schema); SYNONYMS map the
words a model tends to use ("photon", "gene") onto them for hand-written storyboards.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import tools

ICONS = {
    # physics
    "atom": "atom / particle / nucleus", "sparkles": "photon / light quantum / emission",
    "zap": "energy / charge / spark", "waves": "wave / oscillation / signal", "magnet": "magnet / field",
    "orbit": "orbit / system", "sun": "light source / star", "radio-tower": "emitter / transmitter",
    "satellite-dish": "detector / receiver", "radar": "measurement / scan", "gauge": "meter / reading",
    "thermometer": "temperature", "scale": "balance / comparison / equilibrium", "target": "goal / hit",
    "telescope": "observation", "microscope": "microscopic observation", "rotate-cw": "rotation / spin",
    "move-right": "motion / direction", "circle-dot": "point / state", "infinity": "infinite / unbounded",
    "sigma": "sum / statistics", "pi": "mathematics", "variable": "variable / parameter",
    # biology / genomics
    "dna": "dna / gene / genome", "microscope-2": "", "flask-conical": "experiment / lab / sample",
    "test-tube": "sample / assay", "pill": "drug / treatment", "syringe": "dose / injection",
    "heart-pulse": "health / physiology", "brain": "brain / cognition", "bug": "pathogen / microbe",
    "leaf": "plant / ecology", "sprout": "growth / development", "bone": "tissue / anatomy",
    "dna-off": "mutation / knockout", "virus": "virus / infection", "cell": "",
    # data / statistics / computing
    "database": "data / dataset", "table": "table / records", "chart-line": "trend / time series",
    "chart-bar": "counts / comparison", "chart-scatter": "correlation / scatter", "chart-pie": "proportion / share",
    "sigma-square": "", "binary": "digital / bits", "cpu": "computation / processor", "server": "server / storage",
    "network": "network / graph", "git-branch": "branching / alternatives", "git-merge": "merging / combination",
    "filter": "selection / filtering", "funnel": "narrowing / pipeline", "shuffle": "randomisation",
    "dice-5": "chance / probability", "repeat": "repetition / trials", "list-checks": "criteria / checklist",
    "search": "search / test", "calculator": "calculation", "function-square": "model / function",
    # economics / society
    "dollar-sign": "money / price", "coins": "money / revenue", "banknote": "payment / cash",
    "landmark": "institution / government", "building-2": "firm / company", "factory": "industry / production",
    "store": "market / shop", "shopping-cart": "consumer / demand", "handshake": "agreement / trade",
    "trending-up": "increase / growth", "trending-down": "decrease / decline", "percent": "rate / percentage",
    "piggy-bank": "savings", "briefcase": "business / work", "truck": "supply / transport", "globe": "world / global",
    # people / agents
    "user": "person / agent / observer", "users": "group / population / sample", "user-check": "participant",
    "graduation-cap": "education / researcher", "book-open": "theory / literature", "scroll": "paper / record",
    # generic concepts
    "lightbulb": "idea / insight", "eye": "observation / visibility", "lock": "constraint / secret",
    "key": "key / access", "shield": "protection / robustness", "triangle-alert": "warning / limitation",
    "circle-help": "question / unknown", "circle-check": "confirmed / true", "circle-x": "rejected / false",
    "box": "object / container", "boxes": "objects / components", "layers": "layers / levels",
    "puzzle": "piece / component", "settings": "mechanism / process", "cog": "mechanism", "clock": "time / timing",
    "hourglass": "duration / waiting", "calendar": "date / schedule", "map": "map / space", "compass": "direction",
    "link": "connection / correlation", "unlink": "independence / disconnection", "arrow-left-right": "exchange",
    "split": "split / divergence", "merge": "convergence", "ruler": "measurement / length",
    "flag": "milestone", "trophy": "result / winner", "message-square": "communication / message",
    "send": "transmit / send", "inbox": "receive / input", "file-text": "document / report", "hash": "number / count",
    "equal": "equality", "equal-not": "inequality / difference", "plus": "addition", "minus": "subtraction",
    "x": "multiplication / cross", "divide": "division / ratio",
}
ICONS = {k: v for k, v in ICONS.items() if v}          # drop placeholders

SYNONYMS = {
    "photon": "sparkles", "light": "sun", "laser": "zap", "particle": "atom", "electron": "atom",
    "detector": "satellite-dish", "source": "radio-tower", "emitter": "radio-tower", "field": "magnet",
    "spin": "rotate-cw", "gene": "dna", "genome": "dna", "probe": "test-tube", "sample": "users",
    "experiment": "flask-conical", "data": "database", "dataset": "database", "model": "function-square",
    "probability": "dice-5", "random": "shuffle", "correlation": "link", "money": "coins", "price": "dollar-sign",
    "firm": "building-2", "company": "building-2", "market": "store", "government": "landmark",
    "person": "user", "people": "users", "population": "users", "time": "clock", "idea": "lightbulb",
    "warning": "triangle-alert", "result": "trophy", "measurement": "gauge", "computer": "cpu",
}


def normalise(name: str | None) -> str:
    n = (name or "").strip().lower().replace("_", "-").replace(" ", "-")
    if n in ICONS:
        return n
    if n in SYNONYMS:
        return SYNONYMS[n]
    for word in re.split(r"[-/]", n):
        if word in SYNONYMS:
            return SYNONYMS[word]
    return "circle-dot"


@lru_cache(maxsize=None)
def svg_inner(name: str) -> str:
    """Inner SVG markup (paths) of a Lucide icon, each path normalised to pathLength=1 for draw-on."""
    f = tools.renderer_dir() / "node_modules" / "lucide-static" / "icons" / f"{normalise(name)}.svg"
    if not f.exists():
        f = f.with_name("circle-dot.svg")
    body = f.read_text(encoding="utf-8")
    body = body[body.index(">", body.index("<svg")) + 1: body.rindex("</svg>")]
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    return re.sub(r"<(path|circle|line|polyline|polygon|rect|ellipse)\b", r'<\1 pathLength="1"', body)


def prompt_list() -> str:
    return ", ".join(sorted(ICONS))
