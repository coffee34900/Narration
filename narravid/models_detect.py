"""Detect local LLMs (Ollama, LM Studio, GGUF files, Hugging Face cache) and pick the
instruction-tuned chat model with the fewest parameters.

Parameter counts come from metadata wherever possible:
  Ollama       /api/show  model_info["general.parameter_count"]
  GGUF         sum of tensor element counts read from the file header
  HF cache     sum of tensor shapes in *.safetensors headers
Only if metadata is unavailable is the count parsed from the name (flagged as such).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import urllib.request
from pathlib import Path

EXCLUDE_RE = re.compile(r"embed|rerank|bge|e5-|minilm|nomic|whisper|wav2vec|clip|siglip|tts|"
                        r"speech|vision-encoder|mmproj|ocr", re.I)


def _get(url: str, timeout: float = 3.0, data: dict | None = None):
    from .llm import ensure_local
    ensure_local(url)
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _params_from_name(name: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*([bm])\b", name.lower().replace("_", " ").replace("-", " "))
    if not m:
        return None
    v = float(m.group(1))
    return v * 1e9 if m.group(2) == "b" else v * 1e6


# ------------------------------------------------------------------ Ollama
def ollama_models(base_url: str = "http://localhost:11434") -> list[dict]:
    out = []
    try:
        tags = _get(f"{base_url}/api/tags")["models"]
    except Exception:  # noqa: BLE001
        tags = _ollama_cli_list()
    for t in tags:
        name = t.get("name") or t.get("model")
        entry = {"source": "ollama", "backend": "ollama", "name": name, "params": None,
                 "params_from": None, "capabilities": [], "quant": None, "chat": None, "note": ""}
        try:
            info = _get(f"{base_url}/api/show", data={"model": name}, timeout=10)
            pc = (info.get("model_info") or {}).get("general.parameter_count")
            if pc:
                entry["params"], entry["params_from"] = float(pc), "metadata (general.parameter_count)"
            caps = info.get("capabilities") or []
            entry["capabilities"] = caps
            entry["quant"] = (info.get("details") or {}).get("quantization_level")
            has_template = bool(info.get("template"))
            entry["chat"] = ("completion" in caps if caps else has_template) and has_template
        except Exception as e:  # noqa: BLE001
            entry["note"] = f"show failed: {e}"
        if entry["params"] is None:
            ps = (t.get("details") or {}).get("parameter_size")
            if ps:
                entry["params"], entry["params_from"] = _params_from_name(ps + " "), "ollama details.parameter_size"
        if entry["params"] is None:
            entry["params"], entry["params_from"] = _params_from_name(name), "name (metadata unavailable)"
        out.append(entry)
    return out


def _ollama_cli_list() -> list[dict]:
    exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    if not Path(exe).exists():
        return []
    p = subprocess.run([exe, "list"], capture_output=True, text=True)
    rows = p.stdout.strip().splitlines()[1:]
    return [{"name": r.split()[0]} for r in rows if r.strip()]


# ------------------------------------------------------------------ LM Studio
def lmstudio_models(base_url: str = "http://localhost:1234") -> list[dict]:
    out = []
    try:
        data = _get(f"{base_url}/v1/models")["data"]
        for m in data:
            out.append({"source": "lmstudio-server", "backend": "openai", "name": m["id"], "params": _params_from_name(m["id"]),
                        "params_from": "name (server API has no metadata)", "capabilities": [], "chat": None,
                        "quant": None, "note": "served at " + base_url})
    except Exception:  # noqa: BLE001
        pass
    lms = shutil.which("lms")
    if lms:
        try:
            p = subprocess.run([lms, "ls", "--json"], capture_output=True, text=True, timeout=20)
            for m in json.loads(p.stdout or "[]"):
                out.append({"source": "lms", "backend": "openai", "name": m.get("modelKey") or m.get("path"),
                            "params": _params_from_name(str(m.get("paramsString") or m.get("path"))),
                            "params_from": "lms paramsString", "capabilities": [], "chat": m.get("type") == "llm",
                            "quant": None, "note": ""})
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------ GGUF
GGUF_TYPES = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


def gguf_info(path: Path) -> dict:
    """Read GGUF metadata and count parameters from tensor shapes (no tensor data read)."""
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version, = struct.unpack("<I", f.read(4))
        n_tensors, n_kv = struct.unpack("<QQ", f.read(16))

        def rstr():
            n, = struct.unpack("<Q", f.read(8))
            return f.read(n).decode("utf-8", errors="replace")

        def rval(t):
            if t == 8:
                return rstr()
            if t == 9:
                et, n = struct.unpack("<IQ", f.read(12))
                if et in GGUF_TYPES:
                    sz = struct.calcsize("<" + GGUF_TYPES[et])
                    if n > 64:                 # skip big arrays (tokenizer vocab)
                        f.seek(sz * n, 1)
                        return f"<array {n}>"
                    return [struct.unpack("<" + GGUF_TYPES[et], f.read(sz))[0] for _ in range(n)]
                vals = [rval(et) for _ in range(n)]
                return vals if n <= 64 else f"<array {n}>"
            fmt = "<" + GGUF_TYPES[t]
            return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]

        meta = {}
        for _ in range(n_kv):
            k = rstr()
            t, = struct.unpack("<I", f.read(4))
            meta[k] = rval(t)
        total = 0
        for _ in range(n_tensors):
            rstr()
            nd, = struct.unpack("<I", f.read(4))
            dims = struct.unpack("<" + "Q" * nd, f.read(8 * nd))
            f.read(4 + 8)               # type + offset
            p = 1
            for d in dims:
                p *= d
            total += p
    return {"version": version, "meta": {k: v for k, v in meta.items() if not str(v).startswith("<array")},
            "params": total}


def gguf_models(extra_dirs: list[Path] | None = None) -> list[dict]:
    home = Path.home()
    dirs = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models", home / "models",
            home / ".cache" / "llama.cpp", home / "Downloads", Path("C:/models")] + (extra_dirs or [])
    out, seen = [], set()
    for d in dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.gguf"):
            if f in seen or "mmproj" in f.name.lower():
                continue
            seen.add(f)
            e = {"source": "gguf", "backend": "openai", "name": f.name, "path": str(f), "params": None,
                 "params_from": None, "capabilities": [], "chat": None, "quant": None, "note": ""}
            try:
                info = gguf_info(f)
                e["params"], e["params_from"] = float(info["params"]), "metadata (GGUF tensor shapes)"
                e["chat"] = "tokenizer.chat_template" in info["meta"]
                e["arch"] = info["meta"].get("general.architecture")
            except Exception as ex:  # noqa: BLE001
                e["params"], e["params_from"] = _params_from_name(f.name), "name (metadata unreadable)"
                e["note"] = str(ex)[:100]
            e["note"] += " (needs a llama.cpp/LM Studio server to be used)"
            out.append(e)
    return out


# ------------------------------------------------------------------ Hugging Face cache
def hf_models() -> list[dict]:
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    out = []
    if not hub.exists():
        return out
    for repo in hub.glob("models--*"):
        name = repo.name[len("models--"):].replace("--", "/")
        snaps = sorted((repo / "snapshots").glob("*"))
        if not snaps:
            continue
        snap = snaps[-1]
        e = {"source": "huggingface", "backend": "none", "name": name, "params": None, "params_from": None,
             "capabilities": [], "chat": None, "quant": None, "note": "transformers model (no local server)"}
        total = 0
        for st in snap.glob("*.safetensors"):
            try:
                with open(st, "rb") as fh:
                    n, = struct.unpack("<Q", fh.read(8))
                    header = json.loads(fh.read(n))
                for k, v in header.items():
                    if k != "__metadata__":
                        p = 1
                        for d in v["shape"]:
                            p *= d
                        total += p
            except Exception:  # noqa: BLE001
                pass
        if total:
            e["params"], e["params_from"] = float(total), "metadata (safetensors headers)"
        else:
            e["params"], e["params_from"] = _params_from_name(name), "name (metadata unavailable)"
        tok = snap / "tokenizer_config.json"
        if tok.exists():
            try:
                e["chat"] = "chat_template" in json.loads(tok.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        out.append(e)
    return out


# ------------------------------------------------------------------ selection
def classify(e: dict) -> tuple[bool, str]:
    caps = set(e.get("capabilities") or [])
    if EXCLUDE_RE.search(e["name"]):
        return False, "embedding / reranker / speech / vision-only (by name)"
    if caps and "completion" not in caps:
        return False, f"no text completion capability ({', '.join(sorted(caps))})"
    if e.get("chat") is False:
        return False, "no chat template (base model, not instruction-tuned)"
    if e.get("backend") in ("none",):
        return False, "no local server backend to talk to"
    if e.get("source") == "gguf":
        return False, "GGUF file only — load it in LM Studio / llama.cpp server to use it"
    if e.get("params") is None:
        return False, "parameter count unknown"
    return True, "usable chat model"


def detect_all(ollama_url: str = "http://localhost:11434") -> dict:
    found = ollama_models(ollama_url) + lmstudio_models() + gguf_models() + hf_models()
    for e in found:
        e["usable"], e["why"] = classify(e)
    usable = [e for e in found if e["usable"]]
    chosen = min(usable, key=lambda e: e["params"]) if usable else None
    reason = (f"{chosen['name']} has the lowest parameter count ({chosen['params'] / 1e9:.2f}B, from "
              f"{chosen['params_from']}) among {len(usable)} usable instruction-tuned chat model(s).") \
        if chosen else "No usable chat model found."
    return {"models": found, "chosen": chosen, "reason": reason}


def format_table(result: dict) -> str:
    rows = [("source", "model", "params", "params from", "capabilities", "usable", "why")]
    for e in result["models"]:
        rows.append((e["source"], e["name"], f"{e['params'] / 1e9:.2f}B" if e["params"] else "?",
                     e["params_from"] or "", ",".join(e.get("capabilities") or []),
                     "yes" if e["usable"] else "no", e["why"]))
    widths = [max(len(str(r[c])) for r in rows) for c in range(len(rows[0]))]
    lines = ["  ".join(str(v).ljust(w) for v, w in zip(r, widths)) for r in rows]
    lines.insert(1, "-" * len(lines[0]))
    chosen = result["chosen"]
    lines.append("")
    lines.append(f"CHOSEN: {chosen['name'] if chosen else 'none'} — {result['reason']}")
    return "\n".join(lines)
