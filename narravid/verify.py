"""Stage 3 — script-vs-audio verification.

Finds what the TTS voice said that is not in the script (insertions), passages it said
twice (repetitions) and script sentences it never said (omissions). Nothing is changed
until you decide per issue:

  insertion   cut | add_to_script | ignore
  repetition  cut_second | cut_first | add_to_script | ignore
  omission    remove_from_script | ignore

Cuts are snapped to silence midpoints (ffmpeg silencedetect) and every timestamp is
re-mapped afterwards. If a cut cannot be snapped to silence it is refused, never forced.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .audio import snap_cut

CHOICES = {
    "insertion": ["cut", "add_to_script", "ignore"],
    "repetition": ["cut_second", "cut_first", "add_to_script", "ignore"],
    "omission": ["remove_from_script", "ignore"],
}


def detect(aligned: dict, asr: list[dict], silences: list[tuple[float, float]], cfg: dict) -> list[dict]:
    acfg = cfg["alignment"]
    min_run = int(acfg["min_insertion_words"])
    data, words, sentences = aligned["data"], aligned["words"], aligned["sentences"]
    stoks, atoks, blocks = data["stoks"], data["atoks"], data["blocks"]
    s_text = [t[0] for t in stoks]
    issues: list[dict] = []

    # gaps between consecutive matching blocks
    prev_i, prev_j = 0, 0
    for i, j, n in list(blocks) + [(len(stoks), len(atoks), 0)]:
        sgap, agap = i - prev_i, j - prev_j
        if agap - sgap >= min_run or (agap >= min_run and sgap == 0):
            run = atoks[prev_j:j]
            issue = _classify_run(run, prev_i, i, s_text, stoks, words, asr, acfg)
            issues.append(issue)
        prev_i, prev_j = i + n, j + n

    for s in sentences:
        n_tok = sum(1 for t in stoks if words[t[1]]["sid"] == s["id"])
        if n_tok >= 2 and s["matched_frac"] <= acfg["omission_max_matched"]:
            issues.append({"type": "omission", "sentences": [s["id"]], "script_text": s["text"],
                           "start": s["start"], "end": s["end"],
                           "detail": f"only {round(100 * s['matched_frac'])}% of its words were recognised"})

    window = float(cfg["silence"]["snap_window"])
    for k, iss in enumerate(sorted(issues, key=lambda x: x["start"])):
        iss["id"] = k + 1
        iss["choices"] = CHOICES[iss["type"]]
        iss["decision"] = None
        if iss["type"] == "insertion":
            iss["cut"] = snap_cut(iss["start"], iss["end"], silences, window)
        elif iss["type"] == "repetition":
            # occurrences in time order; "cut_first" / "cut_second" refer to this order
            occ = sorted([[iss["start"], iss["end"]], iss.pop("first_occurrence")])
            iss["occurrences"] = occ
            iss["start"], iss["end"] = occ[0][0], occ[1][1]
            iss["cut_first"] = snap_cut(occ[0][0], occ[0][1], silences, window)
            iss["cut_second"] = snap_cut(occ[1][0], occ[1][1], silences, window)
            cf, cs = iss["cut_first"], iss["cut_second"]
            if iss.pop("occurrences_overlap", False) or (cf["ok"] and cs["ok"] and cf["end"] > cs["start"] + 1e-3):
                msg = "the two occurrences overlap; adjust the cut range manually"
                iss["cut_first"] = {**cf, "ok": False, "reason": msg}
                iss["cut_second"] = {**cs, "ok": False, "reason": msg}
    return sorted(issues, key=lambda x: x["id"])


def _classify_run(run, si0, si1, s_text, stoks, words, asr, acfg) -> dict:
    run_text = [t[0] for t in run]
    a_word0, a_word1 = run[0][1], run[-1][2]
    start, end = run[0][3], run[-1][4]
    spoken = " ".join(w["w"] for w in asr[a_word0:a_word1 + 1])
    before_sid = words[stoks[si0 - 1][1]]["sid"] if si0 > 0 else None
    base = {"start": round(start, 3), "end": round(end, 3), "spoken_text": spoken,
            "after_sentence": before_sid, "n_words": a_word1 - a_word0 + 1}
    if si1 > si0:
        base["script_text_in_gap"] = " ".join(words[w]["text"] for w in range(stoks[si0][1], stoks[si1 - 1][2] + 1))
    rep = _repetition(run_text, s_text, acfg["repetition_similarity"])
    if rep:
        r0, r1, s0, s1, ratio = rep
        w0, w1 = stoks[s0][1], stoks[s1 - 1][2]
        second = [run[r0][3], run[r1 - 1][4]]
        first = [words[w0]["start"], words[w1]["end"]]
        sids = sorted({words[w]["sid"] for w in range(w0, w1 + 1)})
        return {**base, "type": "repetition", "sentences": sids, "similarity": ratio,
                "start": round(second[0], 3), "end": round(second[1], 3),
                "spoken_text": " ".join(w["w"] for w in asr[run[r0][1]:run[r1 - 1][2] + 1]),
                # the aligned copy; detect() merges both into time-ordered "occurrences"
                "first_occurrence": [round(first[0], 3), round(first[1], 3)],
                "occurrences_overlap": first[1] > second[0] - 0.05 and first[0] < second[1],
                "script_text": " ".join(words[w]["text"] for w in range(w0, w1 + 1)),
                "detail": "passage recognised twice"}
    return {**base, "type": "insertion", "detail": f"{base['n_words']} recognised words with no script match"}


def _repetition(run_text: list[str], s_text: list[str], min_sim: float):
    """If (most of) an unmatched recognised run repeats a script passage, return
    (run_from, run_to, script_from, script_to, density) for the duplicated span."""
    sm = SequenceMatcher(None, run_text, s_text, autojunk=False)
    m = sm.find_longest_match(0, len(run_text), 0, len(s_text))
    if m.size < 3:
        return None
    lo = max(0, m.b - m.a - 5)
    hi = min(len(s_text), m.b - m.a + len(run_text) + 5)
    blocks = [b for b in SequenceMatcher(None, run_text, s_text[lo:hi], autojunk=False).get_matching_blocks()
              if b.size > 0]
    if not blocks:
        return None
    r0, r1 = blocks[0].a, blocks[-1].a + blocks[-1].size
    s0, s1 = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
    matched = sum(b.size for b in blocks)
    density = matched / max(1, r1 - r0)
    if density >= min_sim and (r1 - r0) >= max(3, len(run_text) // 2):
        return r0, r1, s0, s1, round(density, 2)
    return None


def plan(issues: list[dict]) -> dict:
    """Turn decisions into cuts + script edits. Raises if anything is undecided or unsafe."""
    undecided = [i["id"] for i in issues if not i.get("decision")]
    if undecided:
        raise ValueError(f"Issues without a decision: {undecided}. Decide each one in the Verification panel.")
    cuts, inserts, removes, errors = [], [], [], []
    for iss in issues:
        d = iss["decision"]
        if d not in iss["choices"]:
            errors.append(f"issue {iss['id']}: invalid decision {d}")
            continue
        if d in ("cut", "cut_second", "cut_first"):
            c = iss.get("manual_cut") or iss[d if d != "cut" else "cut"]
            if not c.get("ok"):
                errors.append(f"issue {iss['id']}: cut not on silence ({c.get('reason')}). Adjust the range "
                              "manually or choose another option.")
                continue
            cuts.append((c["start"], c["end"]))
        elif d == "add_to_script":
            inserts.append({"after_sentence": iss.get("after_sentence"), "text": iss["spoken_text"]})
        elif d == "remove_from_script":
            removes.extend(iss["sentences"])
    if errors:
        raise ValueError("; ".join(errors))
    return {"cuts": sorted(cuts), "inserts": inserts, "removes": sorted(set(removes))}


def edit_script(script: dict, inserts: list[dict], removes: list[int]) -> str:
    """Rebuild the script text with insertions/removals; sentence text is kept verbatim."""
    lines: list[str] = []
    for part in script["parts"]:
        body: list[str] = []
        head = None
        pending_top = [x for x in inserts if x["after_sentence"] is None] if part is script["parts"][0] else []
        for x in pending_top:
            body.append(_as_sentence(x["text"]))
        for s in part["sentences"]:
            if s["heading"]:
                head = s["text"]
            elif s["id"] not in removes:
                body.append(s["text"])
            for x in inserts:
                if x["after_sentence"] == s["id"]:
                    body.append(_as_sentence(x["text"]))
        if head:
            lines.append(head)
        lines.append(" ".join(body))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _as_sentence(text: str) -> str:
    t = text.strip()
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if t and t[-1] not in ".!?":
        t += "."
    return t
