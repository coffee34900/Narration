"""Parse a narration script into parts and sentences, preserving exact text.

Format: sections introduced by a heading line such as "Part one. <title>" or
"Part 3: <title>"; sentences end in . ! ? (optionally followed by closing quotes).
Abbreviations ("e.g.", "et al.", initials as in "F. M. Scherer"), decimals ("0.08")
and suffixed numbers ("450k") do not split sentences.
"""
from __future__ import annotations

import re

from .textnorm import UNITS, TENS

ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "cf", "al", "dr", "mr", "mrs", "ms", "prof", "fig", "figs", "eq",
    "eqs", "no", "nos", "approx", "ref", "refs", "st", "jr", "sr", "inc", "ltd", "co", "corp", "p",
    "pp", "vol", "ed", "eds", "ch", "sec", "dept", "univ", "u.s", "u.k", "e.u", "ph.d", "m.sc",
    "b.sc", "a.m", "p.m", "ca", "viz", "resp", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "mt", "min", "max", "est", "esp",
}

PART_RE = re.compile(
    r"^\s*part\s+([0-9]+|[a-z]+(?:[- ][a-z]+)?)\s*[.:\-–—]\s*(.*)$", re.IGNORECASE)
ORDINAL_PART = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
                "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


def part_number(token: str) -> int | None:
    t = token.lower().replace("-", " ").strip()
    if t.isdigit():
        return int(t)
    if t in ORDINAL_PART:
        return ORDINAL_PART[t]
    total = 0
    for w in t.split():
        if w in UNITS:
            total += UNITS[w]
        elif w in TENS:
            total += TENS[w]
        else:
            return None
    return total or None


def _is_boundary(text: str, i: int) -> bool:
    """text[i] is . ! or ? — decide whether a sentence ends here."""
    ch = text[i]
    j = i + 1
    while j < len(text) and text[j] in "\"'”’)]":
        j += 1
    if j < len(text) and not text[j].isspace():
        return False                     # 0.08, e.g.x, U.S.A
    if j >= len(text):
        return True
    if ch in "!?":
        return True
    # word before the period
    k = i - 1
    while k >= 0 and not text[k].isspace():
        k -= 1
    word = text[k + 1:i].strip("\"'(“‘[")
    low = word.lower()
    if low in ABBREVIATIONS:
        return False
    if re.fullmatch(r"[A-Z]", word):                 # initial: F. M. Scherer
        return False
    if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", word):  # e.g / i.e / U.S
        return False
    # next non-space char: a sentence should start with uppercase, digit or quote
    n = j
    while n < len(text) and text[n].isspace():
        n += 1
    if n < len(text) and text[n].islower():
        return False
    return True


def split_sentences(paragraph: str) -> list[str]:
    text = re.sub(r"\s+", " ", paragraph).strip()
    out, start = [], 0
    for i, ch in enumerate(text):
        if ch in ".!?" and _is_boundary(text, i):
            j = i + 1
            while j < len(text) and text[j] in "\"'”’)]":
                j += 1
            s = text[start:j].strip()
            if s:
                out.append(s)
            start = j
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def parse_script(text: str) -> dict:
    """Return {"parts": [{"part": n, "title": str, "sentences": [{"id", "text", "heading"}]}]}."""
    text = text.replace("\r\n", "\n").replace("﻿", "")
    parts: list[dict] = []
    current: dict | None = None
    paragraph: list[str] = []
    sid = 0

    def flush():
        nonlocal sid
        if not paragraph:
            return
        para = " ".join(paragraph)
        paragraph.clear()
        target = current
        if target is None:
            target = _new_part(parts, 0, "Introduction")
        for s in split_sentences(para):
            sid += 1
            target["sentences"].append({"id": sid, "text": s, "heading": False})

    for line in text.split("\n"):
        m = PART_RE.match(line)
        num = part_number(m.group(1)) if m else None
        # A real section heading counts upwards. "Part 2" after Part 3 (e.g. "as we saw in
        # Part 2, ...") is an ordinary sentence; a repeated part number would otherwise
        # make two sections share an identity and break timing.
        if num is not None and parts and num <= max(p["part"] for p in parts):
            num = None
        if m and num is not None:
            flush()
            rest = m.group(2).strip()
            title, body = _split_heading(rest)
            current = _new_part(parts, num, title.rstrip(".!?:") if title else f"Part {num}")
            sid += 1
            heading_text = line.strip()[: len(line.strip()) - len(body)].strip() if body else line.strip()
            current["sentences"].append({"id": sid, "text": heading_text, "heading": True})
            if body:
                paragraph.append(body)
            continue
        if not line.strip():
            flush()
            continue
        paragraph.append(line.strip())
    flush()
    parts = [p for p in parts if p["sentences"]]
    return {"parts": parts}


def _new_part(parts: list[dict], num: int, title: str) -> dict:
    p = {"part": num, "title": title, "sentences": []}
    parts.append(p)
    return p


def _split_heading(rest: str) -> tuple[str, str]:
    """'Classical limit. Body text...' -> ('Classical limit.', 'Body text...')."""
    if not rest:
        return "", ""
    sents = split_sentences(rest)
    if len(sents) <= 1:
        return rest, ""
    title = sents[0]
    if len(title.split()) > 15:          # no clear title sentence: treat whole line as body
        return "", rest
    return title, rest[len(title):].strip()


def all_sentences(script: dict) -> list[dict]:
    out = []
    for p in script["parts"]:
        for s in p["sentences"]:
            out.append({**s, "part": p["part"]})
    return out
