"""Captions from word timestamps: one line at a time, <= max_chars, breaking on punctuation.

Caption text is the exact script text (not the recogniser's), timed by alignment.
"""
from __future__ import annotations

PUNCT_STRONG = ".!?"
PUNCT_WEAK = ",;:—–)"


def segment(words: list[dict], max_chars: int = 50, min_dur: float = 0.8) -> list[dict]:
    """Group words into caption cues. Never crosses a sentence boundary."""
    cues: list[dict] = []
    i, n = 0, len(words)
    while i < n:
        sid = words[i]["sid"]
        j = i
        length = 0
        last_weak = None
        while j < n and words[j]["sid"] == sid:
            add = len(words[j]["text"]) + (1 if j > i else 0)
            if length + add > max_chars and j > i:
                break
            length += add
            if words[j]["text"][-1:] in PUNCT_WEAK:
                last_weak = j
            j += 1
        end = j
        sentence_ends = end >= n or words[end]["sid"] != sid
        if not sentence_ends and last_weak is not None and last_weak - i >= 2:
            end = last_weak + 1           # prefer breaking after a comma/semicolon
        elif not sentence_ends and end - i >= 4:
            # avoid leaving a dangling 1-2 word remainder of the sentence
            rest = 0
            k = end
            while k < n and words[k]["sid"] == sid:
                rest += 1
                k += 1
            if rest <= 2:
                end = max(i + 1, end - (3 - rest))
        seg = words[i:end]
        cues.append({"start": seg[0]["start"], "end": seg[-1]["end"],
                     "text": " ".join(w["text"] for w in seg), "sid": sid})
        i = end
    # timing polish: minimum duration, close tiny gaps, never overlap
    for k, c in enumerate(cues):
        nxt = cues[k + 1]["start"] if k + 1 < len(cues) else None
        if c["end"] - c["start"] < min_dur:
            c["end"] = c["start"] + min_dur
        if nxt is not None:
            if nxt - c["end"] < 0.35:
                c["end"] = nxt
            c["end"] = min(c["end"], nxt)
        c["end"] = max(c["end"], c["start"] + 0.05)
    return cues


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(cues: list[dict]) -> str:
    out = []
    for k, c in enumerate(cues, 1):
        out.append(f"{k}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


def to_ass(cues: list[dict], width: int = 1920, height: int = 1080, font: str = "Inter",
           size: int = 44, margin_v: int = 64) -> str:
    # colours are &HAABBGGRR; BorderStyle 3 = opaque box behind the text (readable on any frame)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00F4ECE8,&H000000FF,&H40120D08,&H40120D08,0,0,0,0,100,100,0,0,3,14,0,2,160,160,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        text = c["text"].replace("\\", "\\\\").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"
