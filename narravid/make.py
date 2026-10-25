"""One-shot video maker: script + audio in, MP4 + SRT out. No projects, no history.

Each run works in a fresh temporary folder that is deleted afterwards; only the finished
video and captions are kept (in the outputs/ folder next to the app). The narration is generated from the
script, so it is used as recorded: speech recognition only provides the timing. No questions.

Accuracy is kept automatically: a scene that fails a fact check or the layout check is
replaced by a plain version quoting the script, so no unchecked claim reaches the video.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import config, tools
from .pipeline import new_project, run_stage
from .schema import validate_scene
from .storyboard import shorten

STEPS = [
    ("listen", "Timing your audio"),
    ("plan", "Planning the visuals"),
    ("facts", "Double-checking every fact"),
    ("build", "Building your video"),
]
OUTPUT_DIR = config.ROOT / "outputs"          # finished videos + captions are saved here automatically
TMP_PREFIX = "narravid-job-"


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, script_name: str):
        self.id = uuid.uuid4().hex[:8]
        self.name = re.sub(r"[^\w\- ]+", "", Path(script_name).stem).strip() or "video"
        self.status = "running"          # running | done | error | cancelled
        self.step = 0
        self.step_progress = 0.0
        self.message = "Getting started…"
        self.notes: list[str] = []       # plain-language things we fixed for you
        self.result: dict | None = None
        self.error: str | None = None
        self.started = time.time()
        self.estimates = {k: 5.0 for k, _ in STEPS}
        self.work: Path | None = None
        self._cancel = False
        self._recent: list[str] = []          # last log lines, only written out if the video fails
        self._chunks: list[str] = []
        self._n_scenes_expected = 0
        self._scenes_done = 0

    # ---------------------------------------------------------------- status for the UI
    def eta_seconds(self) -> int | None:
        if self.status != "running":
            return None
        keys = [k for k, _ in STEPS]
        rem = self.estimates[keys[self.step]] * (1 - self.step_progress)
        rem += sum(self.estimates[k] for k in keys[self.step + 1:])
        return int(rem)

    def overall(self) -> float:
        keys = [k for k, _ in STEPS]
        total = sum(self.estimates.values()) or 1
        done = sum(self.estimates[k] for k in keys[:self.step]) + self.estimates[keys[self.step]] * self.step_progress
        return 1.0 if self.status == "done" else min(0.99, done / total)

    def view(self) -> dict:
        return {"id": self.id, "status": self.status, "step": self.step, "steps": [t for _, t in STEPS],
                "message": self.message, "progress": round(self.overall(), 3), "eta": self.eta_seconds(),
                "result": self.result, "error": self.error, "notes": self.notes,
                "elapsed": int(time.time() - self.started)}

    def cancel(self) -> None:
        self._cancel = True

    # ---------------------------------------------------------------- internals
    def _go(self, step: str, message: str) -> None:
        if self._cancel:
            raise Cancelled()
        self.step = [k for k, _ in STEPS].index(step)
        self.step_progress = 0.0
        self.message = message

    def _log(self, msg: str) -> None:
        """Turn pipeline log lines into sub-step progress."""
        self._recent.append(msg[:2000])
        del self._recent[:-300]
        m = re.search(r"ASR: ([\d.]+) / ([\d.]+) min", msg)
        if m:
            self.step_progress = min(1.0, float(m.group(1)) / max(0.01, float(m.group(2))))
        m = re.search(r"Render: (part_\d+) \((\d+) frames\)", msg)
        if m and self._chunks:
            idx = self._chunks.index(m.group(1)) if m.group(1) in self._chunks else 0
            self.step_progress = 0.1 + 0.8 * idx / len(self._chunks)
            self.message = f"Building your video (part {idx + 1} of {len(self._chunks)})"
        if msg.lstrip().startswith("scene ") and self._n_scenes_expected:
            self._scenes_done += 1
            self.step_progress = min(0.95, self._scenes_done / self._n_scenes_expected)
        if self._cancel:
            raise Cancelled()


UPLOAD_PREFIX = "narravid-upload-"


def cleanup_old_jobs() -> None:
    """Each video is independent: at start-up, remove work/upload folders left by a crash."""
    for pat in (TMP_PREFIX + "*", UPLOAD_PREFIX + "*"):
        for d in Path(tempfile.gettempdir()).glob(pat):
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- automatic model choice
def pick_llm(cfg: dict) -> str | None:
    """Use the configured local model if it is installed, else the smallest local chat model."""
    from . import models_detect
    try:
        res = models_detect.detect_all(cfg["llm"]["base_url"] if cfg["llm"]["backend"] == "ollama"
                                       else "http://localhost:11434")
    except Exception:  # noqa: BLE001
        return None
    usable = [m for m in res["models"] if m["usable"]]
    current = next((m for m in usable if m["name"] == cfg["llm"]["model"]), None)
    chosen = current or res["chosen"]
    if not chosen:
        return None
    if not current:
        config.update({"llm": {"backend": chosen["backend"], "model": chosen["name"]},
                       "model_selection": {"reason": res["reason"]}})
    return chosen["name"]


# ---------------------------------------------------------------- automatic accuracy fixes
def plain_scene(scene: dict, script: dict, cfg: dict, level: int) -> dict:
    """A safe replacement built only from the script's own words.

    level 0: kinetic typography — the narrated sentences themselves, word by word
    level 1: just the part title (always traceable)
    """
    from .storyboard import name_words, sentence_case
    part = next(p for p in script["parts"] if p["part"] == scene["part"])
    sb = cfg["storyboard"]
    title = sentence_case(shorten(part["title"], sb["max_title_words"]), name_words(script))
    base = {"id": scene["id"], "part": scene["part"], "sentences": scene["sentences"], "tag": "none",
            "source": "plain", "approved": None}
    if scene["template"] == "part_title":
        return {**scene, "title": title}
    if level == 0:
        return {**base, "template": "spoken", "title": "", "items": []}
    return {**base, "template": "title_card", "title": title, "items": []}


def repair_scene(scene: dict, bad_fields: set[str], script: dict, cfg: dict) -> dict | None:
    """Remove only the parts that failed (a label, a node, an item) and keep the visual.

    Returns None when the problem cannot be cut out cleanly (then a plain slide is used).
    """
    import copy
    s = copy.deepcopy(scene)
    if not bad_fields or "" in bad_fields:
        return None                                  # a rule broken by the whole scene
    drop_nodes, drop_items, drop_col_items = set(), set(), set()
    for f in bad_fields:
        if m := re.fullmatch(r"nodes\[(\d+)\]\.note", f):
            s["nodes"][int(m.group(1))].pop("note", None)
        elif m := re.fullmatch(r"nodes\[(\d+)\]\.label", f):
            drop_nodes.add(s["nodes"][int(m.group(1))]["id"])
        elif m := re.fullmatch(r"edges\[(\d+)\]\.label", f):
            s["edges"][int(m.group(1))]["label"] = ""
        elif m := re.fullmatch(r"items\[(\d+)\]\.\w+", f):
            drop_items.add(int(m.group(1)))
        elif m := re.fullmatch(r"columns\[(\d+)\]\.items\[(\d+)\]\.text", f):
            drop_col_items.add((int(m.group(1)), int(m.group(2))))
        elif f == "title":
            from .storyboard import name_words, sentence_case
            part = next(p for p in script["parts"] if p["part"] == s["part"])
            s["title"] = sentence_case(shorten(part["title"], cfg["storyboard"]["max_title_words"]), name_words(script))
        elif f == "subtitle":
            s["subtitle"] = ""
        else:
            return None                              # value, label, math, table cell, quote, axis…
    if drop_nodes:
        s["nodes"] = [n for n in s["nodes"] if n["id"] not in drop_nodes]
        s["edges"] = [e for e in s.get("edges") or [] if e["from"] not in drop_nodes and e["to"] not in drop_nodes]
        s["focus"] = [f for f in s.get("focus") or [] if f["node"] not in drop_nodes]
    if drop_items:
        s["items"] = [it for k, it in enumerate(s.get("items") or []) if k not in drop_items]
    for c, i in sorted(drop_col_items, reverse=True):
        del s["columns"][c]["items"][i]
    s["source"] = "repaired"
    s["approved"] = None
    return None if validate_scene(s, cfg) else s


def make_safe(pr, cfg: dict, bad: dict[str, set[str]], level: int) -> int:
    """level -1: repair only what failed · 0: plain verbatim bullets · 1: title card."""
    sb = pr.read_json("storyboard.json")
    script = pr.read_json("script.json")
    n = 0
    for i, s in enumerate(sb["scenes"]):
        if s["id"] not in bad:
            continue
        new = repair_scene(s, bad[s["id"]], script, cfg) if level < 0 else None
        if new is None:
            new = plain_scene(s, script, cfg, max(level, 0))
            if max(level, 0) == 0 and validate_scene(new, cfg):
                new = plain_scene(s, script, cfg, 1)
        sb["scenes"][i] = new
        n += 1
    pr.write_json("storyboard.json", sb)
    return n


def fix_facts(pr, cfg: dict, log=print) -> int:
    """Run the checks and auto-replace every flagged scene, stricter each round. Returns scenes fixed."""
    fixed = 0
    for level in (-1, 0, 1):
        run_stage(pr, "checks", log, use_llm=False)
        ck = pr.read_json("checks.json")
        if not ck["flagged"]:
            break
        fixed += make_safe(pr, cfg, _check_failures(ck), level)
    run_stage(pr, "checks", log, use_llm=False)
    ck = pr.read_json("checks.json")
    if ck["flagged"] or [c for c in ck["coverage"] if c["level"] == "error"]:
        raise RuntimeError("could not make every scene match the script")
    return fixed


def _check_failures(ck: dict) -> dict[str, set[str]]:
    return {sid: {r.get("field", "") for r in ck["scenes"][sid]["results"] if r["level"] == "error"}
            for sid in ck["flagged"]}


def _layout_failures(lq: dict) -> dict[str, set[str]]:
    """Layout problems → fields to drop. Only connection labels and node notes are cut out;
    anything else (e.g. a node label colliding) makes the scene fall back."""
    cuttable = re.compile(r"edges\[\d+\]\.label|nodes\[\d+\]\.note")
    out = {}
    for sid, v in lq["scenes"].items():
        if not v["errors"]:
            continue
        cut: set[str] = set()
        for e in v["errors"]:
            fields = [f for f in (e.get("fields") or []) if f]
            removable = [f for f in fields if cuttable.fullmatch(f)]
            if not removable:                      # nothing small to remove resolves this one
                cut = {""}
                break
            cut.add(removable[0])                  # e.g. an edge label overlapping a node label: drop the edge label
        out[sid] = cut
    return out


# ---------------------------------------------------------------- the whole run
def run(job: Job, audio_path: Path, script_path: Path | None = None, smart: bool = True) -> None:
    job.work = Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    try:
        _run(job, audio_path, script_path, smart)
        job.status = "done"
        job.message = "Your video is ready"
    except Cancelled:
        job.status = "cancelled"
        job.message = "Stopped"
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = friendly_error(e)
        _save_error_report(job, e)
    finally:
        shutil.rmtree(job.work, ignore_errors=True)


ERROR_REPORT = Path(tempfile.gettempdir()) / "narravid-last-error.txt"


def _save_error_report(job: Job, e: Exception) -> None:
    """One overwritten file, only on failure: enough to diagnose without keeping any history."""
    import traceback
    try:
        ERROR_REPORT.write_text(
            f"step: {STEPS[job.step][1]}\nmessage: {job.message}\nerror: {type(e).__name__}: {e}\n\n"
            + "".join(traceback.format_exception(e))[-6000:] + "\n--- recent log ---\n" + "\n".join(job._recent),
            encoding="utf-8")
    except OSError:
        pass


def friendly_error(e: Exception) -> str:
    msg = str(e)
    if "No sentences found" in msg:
        return "No speech was found in the recording (or the script file is empty)."
    if "Invalid data" in msg or "could not find codec" in msg.lower() or "moov atom" in msg:
        return "The audio file could not be read. Please use a WAV, MP3 or M4A file."
    if "Whisper model" in msg:
        return "The speech model is missing. Run scripts\\install.ps1 once, then try again."
    if "HyperFrames" in msg or "hyperframes" in msg:
        return "The video renderer had a problem. Try again; if it keeps happening, run scripts\\install.ps1."
    return f"Something went wrong: {msg[:300]}\n\n(Details were saved to {ERROR_REPORT})"


def transcript_to_script(words: list[dict]) -> str:
    """Rebuild a narration script from the recogniser's words (audio-only mode).

    The narration was generated from a script, so the transcript is that script. Each
    "Part N." / "Part one:" heading is put on its own line so parts are detected as usual.
    """
    if all("r" in w for w in words):
        text = "".join(w["r"] for w in words)         # Whisper's own spacing: "2.83", "75%", "loophole-free"
    else:
        text = ""
        for w in words:                                # fallback: glue punctuation-led tokens to the previous word
            tok = w["w"]
            glue = re.match(r"^[.,;:!?%)\]’'\-]|^\.\d", tok) or text.endswith(("-", "(", "$"))
            text += tok if glue or not text else " " + tok
    text = re.sub(r"\s+", " ", text).strip()
    num = (r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
           r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[- ]\w+)?)")
    # a heading starts a sentence and is followed by a capitalised title: "... end. Part 3. Results ..."
    # ("Part two, as we saw, ..." mid-narration stays an ordinary sentence)
    text = re.sub(rf"(?:^|(?<=[.!?]))\s*(Part\s+{num})\s*[.,:;\u2014-]\s*(?=[A-Z0-9\"\u201c])",
                  lambda m: f"\n\n{m.group(1)}. ", text)
    return text.strip() + "\n"


def _run(job: Job, audio_path: Path, script_path: Path | None, smart: bool) -> None:
    cfg = config.load()
    log = job._log
    words = None
    job._go("listen", "Timing your audio")
    if script_path is None:
        # audio only: the transcript is the script (the speech was generated from it)
        from . import asr, audio
        wav = job.work / "listen.wav"
        audio.to_analysis_wav(audio_path, wav)
        job.estimates["listen"] = max(10, tools.ffprobe_duration(wav) * 0.25)
        words = asr.transcribe(wav, cfg, log)
        if not words:
            raise RuntimeError("No sentences found")
        script_path = job.work / "script.txt"
        script_path.write_text(transcript_to_script(words), encoding="utf-8")
        wav.unlink(missing_ok=True)
    pr = new_project("video", script_path, audio_path, root=job.work)

    # 1 — timing: speech recognition only to find WHEN each word is spoken ------
    # The narration is generated from the script, so it is used exactly as recorded:
    # no script-vs-audio verification, no questions, no cuts.
    info = run_stage(pr, "ingest", log)
    if words is not None:        # reuse the transcript instead of recognising the audio twice
        from .pipeline import asr_key
        pr.write_json("work/asr.json", {"key": asr_key(pr.p("work/analysis.wav"), cfg), "words": words})
    dur = info["duration"]
    n_sent = info["sentences"]
    job.estimates.update({"listen": max(10, dur * 0.25), "plan": (60 + n_sent * 14) if smart else 3,
                          "facts": 5, "build": 20 + dur * 2.0 + 12 * info["parts"]})
    run_stage(pr, "align", log)
    issues = pr.read_json("issues.json")["issues"]
    for i in issues:
        i["decision"] = "ignore"
    pr.write_json("issues.json", {"issues": issues})
    run_stage(pr, "verify", log)        # with every finding ignored: audio copied as is, timings finalised

    # 3 — plan --------------------------------------------------------------------
    job._go("plan", "Planning the visuals")
    use_llm = False
    if smart:
        model = pick_llm(config.load())
        use_llm = model is not None
        if not use_llm:
            job.notes.append("No local AI model was available, so the visuals are simple text slides.")
    job._n_scenes_expected = max(1, n_sent // 2)
    try:
        run_stage(pr, "storyboard", log, use_llm=use_llm)
    except Exception:  # noqa: BLE001 — the LLM is optional; never fail the video because of it
        if not use_llm:
            raise
        job.notes.append("The local AI had trouble, so the visuals are simple text slides.")
        run_stage(pr, "storyboard", log, use_llm=False)

    # 4 — facts: auto-replace anything that is not traceable to the script ---------
    job._go("facts", "Double-checking every fact")
    fixed = fix_facts(pr, cfg, log)
    job.step_progress = 0.5
    if fixed:
        job.notes.append(f"Simplified {fixed} slide(s) so everything on screen matches what is said exactly.")

    # 5 — build: compose + layout QA (auto-simplify) + render + QA ------------------
    job._go("build", "Laying out the slides")
    simplified = 0
    for level in (-1, 0, 1):
        run_stage(pr, "compose", log)
        lq = pr.read_json("qa/layout.json")
        if lq["ok"]:
            break
        simplified += make_safe(pr, cfg, _layout_failures(lq), level)
        # a simplified slide must pass the fact checks again, or rendering refuses it
        simplified += fix_facts(pr, cfg, log)
    else:
        run_stage(pr, "compose", log)
    lq = pr.read_json("qa/layout.json")
    if simplified:
        job.notes.append(f"Simplified {simplified} crowded slide(s) so text never overlaps the captions.")
    job._chunks = [c["id"] for c in pr.read_json("timing.json")["chunks"]]
    job.message = "Building your video"
    out = run_stage(pr, "render", log, override_layout=not lq["ok"], compressed=False)
    job.step_progress = 0.92
    job.message = "Final check"
    from . import qa
    sync = qa.sync_check(pr, cfg)
    if not sync["duration_ok"]:
        job.notes.append("Warning: the video and audio lengths differ slightly.")

    # keep only the results ------------------------------------------------------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video = _unique(OUTPUT_DIR / f"{job.name}.mp4")
    shutil.copy2(out["final"], video)
    srt = video.with_suffix(".srt")
    shutil.copy2(out["srt"], srt)
    result = {"video": str(video), "srt": str(srt), "duration": round(tools.ffprobe_duration(video), 1),
              "size_mb": round(video.stat().st_size / 1048576, 1), "small": []}
    target = float(cfg["render"]["compressed_target_mb"])
    if result["size_mb"] > target:
        job.message = "Making a smaller copy for sharing"
        from . import render
        for k, f in enumerate(render.compress(pr, cfg, log)):
            dst = video.with_name(f"{video.stem} (small{'' if k == 0 else ' ' + str(k + 1)}).mp4")
            shutil.copy2(f, dst)
            result["small"].append(str(dst))
    job.result = result


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    k = 2
    while p.with_name(f"{p.stem} ({k}){p.suffix}").exists():
        k += 1
    return p.with_name(f"{p.stem} ({k}){p.suffix}")


def make_video(audio: Path, script: Path | None = None, smart: bool = True, log=print) -> Job:
    """Command-line / test entry point: runs synchronously. The script is optional."""
    cleanup_old_jobs()
    job = Job((script or audio).name)
    t = threading.Thread(target=run, args=(job, audio, script, smart), daemon=True)
    t.start()
    last = None
    while t.is_alive():
        if job.message != last:
            log(f"[{job.step + 1}/{len(STEPS)}] {job.message}")
            last = job.message
        time.sleep(0.5)
    if job.status == "error":
        log("ERROR: " + (job.error or ""))
    for n in job.notes:
        log("• " + n)
    return job


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="narravid.make", description="Make a video from a narration recording")
    ap.add_argument("audio", type=Path)
    ap.add_argument("--script", type=Path, help="optional: the script text, for exact spelling")
    ap.add_argument("--simple", action="store_true", help="skip the local AI (faster, plain text slides)")
    a = ap.parse_args()
    j = make_video(a.audio, a.script, smart=not a.simple)
    if j.result:
        print("Video:", j.result["video"])
    sys.exit(0 if j.status == "done" else 1)
