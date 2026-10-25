"""Audio helpers: analysis copy, silence map, silence-snapped cuts, timestamp remapping."""
from __future__ import annotations

import re
from pathlib import Path

from . import tools


def to_analysis_wav(src: Path, dst: Path, log=None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
               "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst], log=log)


def to_master_wav(src: Path, dst: Path, log=None) -> None:
    """Lossless copy of the original narration (original rate/channels) used for the final mix."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
               "-vn", "-c:a", "pcm_s24le", dst], log=log)


def detect_silences(wav: Path, noise_db: float = -35, min_d: float = 0.25) -> list[tuple[float, float]]:
    p = tools.run([tools.find("ffmpeg"), "-hide_banner", "-nostats", "-i", wav, "-af",
                   f"silencedetect=noise={noise_db}dB:d={min_d}", "-f", "null", "-"], check=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    dur = tools.ffprobe_duration(wav)
    if len(ends) < len(starts):
        ends.append(dur)
    return [(max(0.0, s), e) for s, e in zip(starts, ends)]


def snap_cut(start: float, end: float, silences: list[tuple[float, float]], window: float) -> dict:
    """Move a proposed cut [start, end] onto silence midpoints.

    The cut start snaps to the silence that is nearest to `start` (within `window` s),
    the end to the silence nearest `end`. If either side has no silence nearby, the cut
    is NOT applied automatically — it is returned with ok=False so you can adjust it.
    """
    def nearest(t: float):
        best = None
        for s, e in silences:
            # distance from t to the silence interval
            d = 0.0 if s <= t <= e else min(abs(t - s), abs(t - e))
            if d <= window and (best is None or d < best[0]):
                best = (d, s, e)
        return best

    a, b = nearest(start), nearest(end)
    if not a or not b:
        return {"ok": False, "start": start, "end": end,
                "reason": "no silence within %.1fs of the %s edge" % (window, "start" if not a else "end")}
    cut_start = (a[1] + a[2]) / 2
    cut_end = (b[1] + b[2]) / 2
    if cut_end - cut_start < 0.2:
        return {"ok": False, "start": start, "end": end, "reason": "snapped cut is empty"}
    return {"ok": True, "start": round(cut_start, 3), "end": round(cut_end, 3)}


def merge_cuts(cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for s, e in sorted(cuts):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def remap_time(t: float, cuts: list[tuple[float, float]]) -> float | None:
    """Map a time in the original audio to the cut audio. None if t lies inside a cut."""
    shift = 0.0
    for s, e in cuts:
        if t >= e:
            shift += e - s
        elif t > s:
            return None
        else:
            break
    return t - shift


def apply_cuts(src: Path, dst: Path, cuts: list[tuple[float, float]], log=None) -> None:
    """Write `src` minus the cut ranges to `dst`, with 10 ms fades at every join."""
    cuts = merge_cuts(cuts)
    if not cuts:
        tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                   "-c:a", "pcm_s24le", dst], log=log)
        return
    dur = tools.ffprobe_duration(src)
    keep, t = [], 0.0
    for s, e in cuts:
        if s > t:
            keep.append((t, s))
        t = e
    if t < dur:
        keep.append((t, dur))
    parts, labels = [], []
    for i, (s, e) in enumerate(keep):
        fade_out_at = max(s, e - 0.01)
        parts.append(f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.01,afade=t=out:st={fade_out_at - s:.6f}:d=0.01[k{i}]")
        labels.append(f"[k{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(keep)}:v=0:a=1[out]"
    tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", src,
               "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", dst], log=log)


def extract_clip(src: Path, dst: Path, start: float, end: float) -> None:
    tools.run([tools.find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
               "-to", f"{end:.3f}", "-i", src, "-c:a", "libmp3lame", "-q:a", "4", dst])
