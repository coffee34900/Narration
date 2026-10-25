"""Stage 2 — align recognised words to the exact script words (no LLM).

Script and ASR are reduced to canonical tokens (textnorm), matched with
difflib.SequenceMatcher (autojunk off), and every script word gets the time of the
recognised token(s) it matched. Unmatched words are interpolated between their
matched neighbours, proportionally to their character length.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .sentences import all_sentences
from .textnorm import Tok, canon_tokens


def script_words(script: dict) -> list[dict]:
    out = []
    for s in all_sentences(script):
        for k, w in enumerate(s["text"].split()):
            out.append({"i": len(out), "sid": s["id"], "k": k, "text": w})
    return out


def _token_times(toks: list[Tok], words: list[dict]) -> list[tuple[float, float]]:
    """Time interval of each ASR token; a word producing several tokens is split evenly."""
    per_word: dict[int, list[int]] = {}
    for ti, t in enumerate(toks):
        per_word.setdefault(t.start, []).append(ti)
    out: list[tuple[float, float]] = [(0.0, 0.0)] * len(toks)
    for wi, tis in per_word.items():
        last = toks[tis[-1]].end
        s, e = words[wi]["s"], words[last]["e"]
        n = len(tis)
        for k, ti in enumerate(tis):
            out[ti] = (s + (e - s) * k / n, s + (e - s) * (k + 1) / n)
    return out


def filter_blocks(blocks: list[tuple[int, int, int]], small: int = 2, gap: int = 3) -> list[tuple[int, int, int]]:
    """Drop short matches stranded inside large unmatched regions.

    SequenceMatcher happily matches an isolated "the" in the middle of an inserted
    passage, which would split a 40-word insertion into short runs and hide it.
    """
    blocks = [b for b in blocks if b[2] > 0]
    keep = []
    for idx, (i, j, n) in enumerate(blocks):
        if n > small:
            keep.append((i, j, n))
            continue
        pi, pj, pn = blocks[idx - 1] if idx > 0 else (0, 0, 0)
        ni, nj, _ = blocks[idx + 1] if idx + 1 < len(blocks) else (i + n, j + n, 0)
        gap_before = max(i - (pi + pn), j - (pj + pn))
        gap_after = max(ni - (i + n), nj - (j + n))
        if gap_before < gap and gap_after < gap:
            keep.append((i, j, n))
    return keep


def align(script: dict, asr: list[dict], duration: float) -> dict:
    words = script_words(script)
    stoks = canon_tokens([w["text"] for w in words])
    atoks = canon_tokens([a["w"] for a in asr])
    sm = SequenceMatcher(None, [t.text for t in stoks], [t.text for t in atoks], autojunk=False)
    blocks = filter_blocks(sm.get_matching_blocks())
    atimes = _token_times(atoks, asr) if atoks else []

    # matched intervals per script word
    word_iv: dict[int, list[float]] = {}
    s_matched = [False] * len(stoks)
    a_matched = [False] * len(atoks)
    for i, j, n in blocks:
        for k in range(n):
            st, at = stoks[i + k], atimes[j + k]
            s_matched[i + k] = True
            a_matched[j + k] = True
            span = list(range(st.start, st.end + 1))
            lens = [max(1, len(words[w]["text"])) for w in span]
            tot, acc = sum(lens), 0
            for w, ln in zip(span, lens):
                s = at[0] + (at[1] - at[0]) * acc / tot
                acc += ln
                e = at[0] + (at[1] - at[0]) * acc / tot
                iv = word_iv.setdefault(w, [s, e])
                iv[0], iv[1] = min(iv[0], s), max(iv[1], e)

    for w in words:
        iv = word_iv.get(w["i"])
        w["matched"] = iv is not None
        if iv:
            w["start"], w["end"] = round(iv[0], 3), round(iv[1], 3)
    _interpolate(words, duration)
    _make_monotonic(words, duration)

    sentences = []
    for s in all_sentences(script):
        ws = [w for w in words if w["sid"] == s["id"]]
        tok_idx = [ti for ti, t in enumerate(stoks) if words[t.start]["sid"] == s["id"]]
        frac = (sum(s_matched[t] for t in tok_idx) / len(tok_idx)) if tok_idx else 1.0
        sentences.append({"id": s["id"], "part": s["part"], "text": s["text"], "heading": s["heading"],
                          "start": ws[0]["start"] if ws else 0.0, "end": ws[-1]["end"] if ws else 0.0,
                          "matched_frac": round(frac, 3)})

    report = {
        "script_tokens": len(stoks), "asr_tokens": len(atoks),
        "script_matched_pct": round(100 * sum(s_matched) / max(1, len(stoks)), 2),
        "asr_matched_pct": round(100 * sum(a_matched) / max(1, len(atoks)), 2),
        "words_matched_pct": round(100 * sum(w["matched"] for w in words) / max(1, len(words)), 2),
        "duration": duration,
        "checks": sanity_checks(sentences, duration),
    }
    data = {
        "stoks": [[t.text, t.start, t.end] for t in stoks],
        "atoks": [[t.text, t.start, t.end, round(atimes[k][0], 3), round(atimes[k][1], 3)]
                  for k, t in enumerate(atoks)],
        "blocks": blocks,
    }
    return {"words": words, "sentences": sentences, "report": report, "data": data}


def _interpolate(words: list[dict], duration: float) -> None:
    n = len(words)
    k = 0
    while k < n:
        if words[k]["matched"]:
            k += 1
            continue
        j = k
        while j < n and not words[j]["matched"]:
            j += 1
        left = words[k - 1]["end"] if k > 0 else 0.0
        right = words[j]["start"] if j < n else duration
        if right < left:
            right = left
        lens = [max(1, len(words[m]["text"])) for m in range(k, j)]
        tot, acc = sum(lens), 0
        for m, ln in zip(range(k, j), lens):
            words[m]["start"] = round(left + (right - left) * acc / tot, 3)
            acc += ln
            words[m]["end"] = round(left + (right - left) * acc / tot, 3)
        k = j


def _make_monotonic(words: list[dict], duration: float) -> None:
    prev = 0.0
    for w in words:
        w["start"] = min(max(w["start"], prev), duration)
        w["end"] = min(max(w["end"], w["start"]), duration)
        prev = w["start"]


def sanity_checks(sentences: list[dict], duration: float) -> list[dict]:
    problems = []
    prev = -1.0
    for s in sentences:
        if s["start"] < prev:
            problems.append({"sentence": s["id"], "problem": "start before previous sentence start"})
        if s["end"] < s["start"]:
            problems.append({"sentence": s["id"], "problem": "end before start"})
        if s["start"] < 0 or s["end"] > duration + 0.05:
            problems.append({"sentence": s["id"], "problem": "outside the audio"})
        prev = s["start"]
    return problems


def remap_asr(asr: list[dict], cuts: list[tuple[float, float]]) -> list[dict]:
    from .audio import remap_time
    out = []
    for w in asr:
        s, e = remap_time(w["s"], cuts), remap_time(w["e"], cuts)
        if s is None or e is None:
            continue
        out.append({**w, "s": round(s, 3), "e": round(e, 3)})
    return out
