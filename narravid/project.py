"""A project is a folder; every stage reads/writes plain files inside it.

projects/<name>/
  input/            original script + audio (never modified)
  work/             analysis WAV, raw ASR words, silence map
  script_final.txt  script after any "add to script as spoken" decisions (starts as a copy)
  script.json       parsed parts -> sentences
  words.json / sentences.json / alignment_report.json
  issues.json       Stage 3 findings + your decisions;  audio/final.wav after cuts
  storyboard.json / checks.json
  composition/      generated HyperFrames projects (one per part + full preview)
  render/           chunks, final MP4/SRT/ASS, compressed copies
  qa/               frames, contact sheets, layout + sync reports
  state.json        per-stage status + input fingerprints (resumability)
  logs/pipeline.log
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from . import config

STAGES = ["ingest", "align", "verify", "storyboard", "checks", "compose", "render", "qa"]

# Files whose content determines whether a stage is up to date.
STAGE_INPUTS: dict[str, list[str]] = {
    "ingest": ["input"],
    "align": ["work/analysis.wav", "script_original.json"],
    "verify": ["issues.json", "align/data.json"],
    "storyboard": ["script.json"],
    "checks": ["storyboard.json", "script.json"],
    "compose": ["storyboard.json", "sentences.json", "words.json", "audio/final.wav"],
    "render": ["timing.json", "captions.ass"],
    "qa": ["render/final.mp4"],
}

_lock = threading.Lock()


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-")
    return s[:60] or "project"


def projects_dir() -> Path:
    d = config.resolve(config.load()["paths"]["projects_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_projects() -> list[str]:
    return sorted(p.name for p in projects_dir().iterdir() if (p / "state.json").exists())


class Project:
    def __init__(self, name: str, root: Path | None = None):
        self.name = slug(name)
        self.dir = (root or projects_dir()) / self.name

    # ----- paths
    def p(self, rel: str) -> Path:
        return self.dir / rel

    def create(self) -> "Project":
        for sub in ("input", "work", "audio", "composition", "render", "qa", "logs"):
            self.p(sub).mkdir(parents=True, exist_ok=True)
        if not self.p("state.json").exists():
            self.write_json("state.json", {"stages": {}, "created": _now()})
        return self

    def exists(self) -> bool:
        return self.p("state.json").exists()

    def input_script(self) -> Path | None:
        hits = sorted(self.p("input").glob("script.*"))
        return hits[0] if hits else None

    def input_audio(self) -> Path | None:
        hits = sorted(self.p("input").glob("audio.*"))
        return hits[0] if hits else None

    # ----- json helpers
    def read_json(self, rel: str, default: Any = None) -> Any:
        f = self.p(rel)
        if not f.exists():
            return default
        return json.loads(f.read_text(encoding="utf-8"))

    def write_json(self, rel: str, data: Any) -> None:
        f = self.p(rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    # ----- logging
    def log(self, msg: str) -> None:
        line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
        with _lock:
            self.p("logs").mkdir(parents=True, exist_ok=True)
            with open(self.p("logs/pipeline.log"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        print(line, flush=True)

    # ----- stage state
    def fingerprint(self, stage: str) -> str:
        h = hashlib.sha1()
        for rel in STAGE_INPUTS.get(stage, []):
            path = self.p(rel)
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            for f in files:
                if f.is_file() and not f.name.endswith(".tmp"):
                    st = f.stat()
                    h.update(f"{f.relative_to(self.dir)}:{st.st_size}:{st.st_mtime_ns}".encode())
        return h.hexdigest()

    def state(self) -> dict:
        return self.read_json("state.json", {"stages": {}})

    def set_stage(self, stage: str, status: str, **extra) -> None:
        with _lock:
            st = self.state()
            entry = st["stages"].get(stage, {})
            entry.update({"status": status, "updated": _now(), **extra})
            if status == "done":
                entry["fingerprint"] = self.fingerprint(stage)
            st["stages"][stage] = entry
            self.write_json("state.json", st)

    def stage_status(self, stage: str) -> str:
        e = self.state()["stages"].get(stage)
        if not e:
            return "pending"
        if e.get("status") == "done" and e.get("fingerprint") != self.fingerprint(stage):
            return "stale"
        return e.get("status", "pending")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")
