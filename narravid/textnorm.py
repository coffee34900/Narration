"""Canonical tokens for alignment and fact checks.

Both the script and the recogniser output are converted to the same canonical
token stream, so "seventy-five percent", "75 percent" and "75%" all become
["75", "percent"], and "1,906" / "one thousand nine hundred and six" become "1906".
Every canonical token remembers which source word(s) it came from, so timings can be
mapped back onto the exact script words.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9, "trillion": 10**12}
ORD_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
             "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
             "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
             "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90, "hundredth": 100,
             "thousandth": 1000, "millionth": 10**6}
SUFFIX_MULT = {"k": 1000, "m": 10**6, "bn": 10**9}
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta",
         "η": "eta", "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
         "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi",
         "ω": "omega", "Δ": "delta", "Σ": "sigma", "Ω": "omega", "Φ": "phi", "Ψ": "psi", "Λ": "lambda",
         "Γ": "gamma", "Θ": "theta", "Π": "pi"}

_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")


@dataclass
class Tok:
    text: str
    start: int   # index of first source word
    end: int     # index of last source word (inclusive)


def fmt_num(v: Decimal | int | float) -> str:
    d = Decimal(str(v))
    if d == d.to_integral_value():
        return str(int(d))
    s = format(d.normalize(), "f")
    return s


def _clean(word: str) -> str:
    w = unicodedata.normalize("NFKC", word)
    w = w.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    w = w.replace("–", "-").replace("—", " ").replace("−", "-")
    return w


def _pieces(word: str) -> list[str]:
    """Split one written word into raw lower-case pieces (before number-word parsing)."""
    w = _clean(word)
    out: list[str] = []
    for g, name in GREEK.items():
        w = w.replace(g, f" {name} ")
    w = w.replace("√", " root ").replace("%", " percent ").replace("&", " and ")
    w = w.replace("°", " degrees ").replace("×", " times ").replace("±", " plus or minus ")
    w = re.sub(r"\$(\d[\d,.]*)", r"\1 dollars", w)
    w = re.sub(r"(?<=\d),(?=\d{3}\b)", "", w)            # 1,906 -> 1906
    for chunk in re.split(r"[\s/]+", w):
        chunk = chunk.strip(",;:!?\"'()[]{}<>*_`~")
        lead_decimal = bool(re.match(r"\.\d", chunk))
        chunk = chunk.strip(".,;:!?\"'()[]{}<>*_`~")
        if lead_decimal:
            chunk = "0." + chunk
        if not chunk:
            continue
        chunk = re.sub(r"'s$", "", chunk.lower())
        # 450k / 2.5m / 3bn
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(k|m|bn)", chunk)
        if m:
            out.append(fmt_num(Decimal(m.group(1)) * SUFFIX_MULT[m.group(2)]))
            continue
        # 1st 2nd 21st
        m = re.fullmatch(r"(\d+)(st|nd|rd|th)", chunk)
        if m:
            out.append(m.group(1) + "th")
            continue
        # digits glued to letters: "2x" "p53" stay as identifiers; "1990s" -> 1990 s
        m = re.fullmatch(r"(\d{4})s", chunk)
        if m:
            out.extend([m.group(1), "s"])
            continue
        for part in re.split(r"(?<=[a-z])-(?=[a-z])|(?<=\d)-(?=[a-z])|(?<=[a-z])-(?=\d)", chunk):
            part = part.strip("-.")
            if part:
                if re.fullmatch(r"\.\d+", part):
                    part = "0" + part
                out.append(part)
    return out


def _is_numword(p: str) -> bool:
    return p in UNITS or p in TENS or p in SCALES


def _parse_number_run(ps: list[str], i: int) -> tuple[str | None, int]:
    """Parse number words starting at ps[i]. Returns (canonical string, next index)."""
    j = i
    if ps[j] == "a" and j + 1 < len(ps) and ps[j + 1] in SCALES:   # "a hundred"
        ps = ps[:j] + ["one"] + ps[j + 1:]
    if ps[j] == "oh" or not (_is_numword(ps[j]) or ps[j] == "point"):
        return None, i
    groups: list[int] = []   # completed values separated by year-style juxtaposition
    total, cur, seen = 0, 0, False
    last_kind = None
    while j < len(ps):
        w = ps[j]
        if w == "oh":          # handled by canon_tokens ("nineteen oh five")
            break
        if w in UNITS:
            v = UNITS[w]
            if last_kind in ("unit", "teen") or (last_kind == "tens" and v >= 10):
                # juxtaposed number ("nineteen ninety", "twenty twenty") -> new group
                groups.append(total + cur)
                total, cur = 0, 0
            cur += v
            last_kind = "teen" if v >= 10 else "unit"
        elif w in TENS:
            if last_kind in ("unit", "teen", "tens"):
                groups.append(total + cur)
                total, cur = 0, 0
            cur += TENS[w]
            last_kind = "tens"
        elif w in SCALES:
            s = SCALES[w]
            if cur == 0:
                cur = 1
            if s == 100:
                cur *= 100
            else:
                total += cur * s
                cur = 0
            last_kind = "scale"
        elif w == "and" and seen and last_kind == "scale" and j + 1 < len(ps) and \
                (ps[j + 1] in UNITS or ps[j + 1] in TENS):
            pass
        elif w == "point" and j + 1 < len(ps) and (ps[j + 1] in UNITS and UNITS[ps[j + 1]] < 10):
            k = j + 1
            digits = ""
            while k < len(ps) and ps[k] in UNITS and UNITS[ps[k]] < 10:
                digits += str(UNITS[ps[k]])
                k += 1
            whole = total + cur
            groups.append(whole)
            val = Decimal(f"{fmt_num(_merge_groups(groups))}.{digits}")
            return fmt_num(val), k
        else:
            break
        seen = True
        j += 1
    if not seen:
        return None, i
    groups.append(total + cur)
    return fmt_num(_merge_groups(groups)), j


def _merge_groups(groups: list[int]) -> int:
    """Year-style juxtaposition: [19, 98] -> 1998, [20, 23] -> 2023, [19, 5] (nineteen oh five) -> 1905."""
    if len(groups) == 1:
        return groups[0]
    if len(groups) == 2 and 10 <= groups[0] <= 99 and 0 <= groups[1] <= 99:
        return groups[0] * 100 + groups[1]
    # anything else: just keep the last group (rare; e.g. a digit-by-digit read-out)
    return int("".join(str(g) for g in groups)) if all(0 <= g <= 9 for g in groups) else groups[-1]


def canon_tokens(words: list[str]) -> list[Tok]:
    raw: list[tuple[str, int]] = []
    for idx, w in enumerate(words):
        for p in _pieces(w):
            raw.append((p, idx))
    ps = [p for p, _ in raw]
    out: list[Tok] = []
    i = 0
    while i < len(ps):
        p, src = raw[i]
        if p in ORD_WORDS and p != "second":
            out.append(Tok(f"{ORD_WORDS[p]}th", src, src))
            i += 1
            continue
        if p == "oh" and out and out[-1].text.isdigit() and i + 1 < len(ps) and ps[i + 1] in UNITS:
            # "nineteen oh five"
            prev = out.pop()
            val = int(prev.text) * 100 + UNITS[ps[i + 1]]
            out.append(Tok(str(val), prev.start, raw[i + 1][1]))
            i += 2
            continue
        num, j = _parse_number_run(ps, i)
        if num is not None and j > i:
            end_src = raw[j - 1][1]
            # compound ordinal: "twenty first" -> 21th
            if j < len(ps) and ps[j] in ORD_WORDS and ORD_WORDS[ps[j]] < 10 and num.isdigit() \
                    and int(num) >= 20 and int(num) % 10 == 0:
                out.append(Tok(f"{int(num) + ORD_WORDS[ps[j]]}th", src, raw[j][1]))
                i = j + 1
                continue
            out.append(Tok(num, src, end_src))
            i = j
            continue
        if _NUM_RE.match(p):
            val = Decimal(p)
            j = i + 1
            if j < len(ps) and ps[j] in SCALES and ps[j] != "hundred":
                val *= SCALES[ps[j]]
                j += 1
            elif j < len(ps) and ps[j] == "k":
                val *= 1000
                j += 1
            out.append(Tok(fmt_num(val), src, raw[j - 1][1]))
            i = j
            continue
        if p in ("per", ) and i + 1 < len(ps) and ps[i + 1] == "cent":
            out.append(Tok("percent", src, raw[i + 1][1]))
            i += 2
            continue
        if p == "the" and i + 2 < len(ps) and ps[i + 1] == "square" and ps[i + 2] == "root":
            i += 1                       # "the square root of two" == "root 2"
            continue
        if p == "square" and i + 1 < len(ps) and ps[i + 1] == "root":
            j = i + 2
            if j < len(ps) and ps[j] == "of":
                j += 1
            out.append(Tok("root", src, raw[j - 1][1]))
            i = j
            continue
        if p in ("um", "uh", "-"):
            i += 1
            continue
        out.append(Tok(p, src, src))
        i += 1
    return out


def canon_text(text: str) -> list[str]:
    return [t.text for t in canon_tokens(text.split())]


# ---------------------------------------------------------------- fact extraction
def is_number(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def number_facts(text: str) -> list[dict]:
    """Numbers in a piece of text, with compound forms (75%, 2√2, √2) where present.

    Returns [{"value": "75", "compound": "75%", "src": "75%"}...]
    """
    words = text.split()
    toks = canon_tokens(words)
    facts = []
    k = 0
    while k < len(toks):
        t = toks[k]
        nxt = toks[k + 1].text if k + 1 < len(toks) else None
        if t.text == "root" and nxt and is_number(nxt):
            facts.append({"value": nxt, "compound": f"√{nxt}", "src": " ".join(words[t.start:toks[k + 1].end + 1])})
            k += 2
            continue
        if is_number(t.text) or re.fullmatch(r"\d+th", t.text):
            compound = None
            end = t.end
            if nxt == "percent":
                compound = f"{t.text}%"
                end = toks[k + 1].end
                k += 1
            elif nxt == "root" and k + 2 < len(toks) and is_number(toks[k + 2].text):
                compound = f"{t.text}√{toks[k + 2].text}"
                end = toks[k + 2].end
                k += 2
            facts.append({"value": t.text, "compound": compound, "src": " ".join(words[t.start:end + 1])})
        k += 1
    return facts


IDENT_RE = re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z][A-Za-z0-9_\-]*\b|\b[A-Z]{2,}[a-z]?\b")


def identifiers(text: str) -> list[str]:
    """Gene / probe / symbol-like tokens: TP53, rs12345, ILMN_1651199, GWAS, mRNA, CHSH."""
    out = []
    for m in IDENT_RE.finditer(_clean(text)):
        tok = m.group(0).strip("-_")
        if len(tok) >= 2:
            out.append(tok)
    out += [m.group(0) for m in re.finditer(r"\b[a-z]+[A-Z][A-Za-z]*\b", text)]   # mRNA, eQTL
    return sorted(set(out))


def proper_nouns(text: str, with_first: bool = False) -> list[str]:
    """Capitalised words that are not the first word of a sentence/clause.

    Title-cased headings would make every word look like a name, so on-screen text
    is expected in sentence case (the storyboard prompt asks for it). With
    `with_first`, first words are returned too (the caller decides whether they
    are ordinary words — short labels such as "Einstein's detector" start with names).
    """
    out = []
    t = _clean(text)
    for seg in re.split(r"(?:^|(?<![A-Z])[.!?]\s+|[:;=]\s+|\s[-–—]\s|\n)", t):
        words = seg.split()
        for i, w in enumerate(words):
            core = w.strip(".,;:!?\"'()[]{}")
            core = re.sub(r"'s$", "", core)
            if (i == 0 and not with_first) or not core or not core[0].isupper():
                continue
            if core.isupper() and len(core) <= 1:
                continue
            out.append(core)
    return sorted(set(out))


def stem(word: str) -> str:
    """Crude English stem for matching on-screen words to the narration (detectors ~ detector)."""
    w = re.sub(r"'s$", "", word.lower().strip(".,;:!?\"'()[]{}"))
    for suf in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


# Frequent words that open a label or title in sentence case and are never names.
COMMON_FIRST = set("""a an the this that these those two three four five one first second new old main key
how why what when where which who whose our their its his her my your some all each every more most less few
many much other another same different both either neither no not only also very just low high large small big
higher lower larger smaller better worse best worst early late final total overall result results effect effects
cause causes evidence measured predicted expected observed classical quantum local global general limit bound
test tests testing source sources data model models method methods system systems process step steps stage
input output signal sample samples group groups level levels rate rates value values number numbers time
start end before after during between against without with within from into onto over under above below
summary recap key definition example examples problem problems question questions answer answers idea ideas
why how so then thus therefore hence because if unless while although""".split())
