"""Load / save config.yaml. Defaults live here; the file on disk overrides them."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "paths": {
        "projects_dir": "projects",
        "ffmpeg": "",          # empty = auto-detect
        "ffprobe": "",
        "node": "",            # empty = tools/node/node.exe or PATH
        "renderer_dir": "renderer",
    },
    "llm": {
        "backend": "ollama",   # ollama | openai  (openai = LM Studio / llama.cpp server)
        "model": "",
        "base_url": "http://localhost:11434",
        "temperature": 0.2,
        "max_retries": 3,
        "timeout_s": 600,
        "think": False,        # disable "thinking" traces for structured output
        "num_ctx": 4096,       # each storyboard call is one part: small context is enough
        "num_gpu": 99,         # Ollama layers on GPU (99 = all); falls back automatically if it won't fit
        "self_check": True,
        "strong_model": "",    # bigger local model for a second pass over failed/weak scenes
    },
    "model_selection": {"reason": "", "detected": []},
    "asr": {
        "engine": "faster-whisper",   # faster-whisper | pocketsphinx
        "model": "small.en",
        "model_dir": "models/whisper",
        "device": "auto",             # auto | cpu | cuda
        "compute_type": "auto",
        "language": "en",
        "beam_size": 5,
    },
    "alignment": {
        "min_insertion_words": 5,      # unmatched recognised run length that counts as an insertion
        "repetition_similarity": 0.6,  # similarity of an inserted run to script text => repetition
        "omission_max_matched": 0.25,  # sentence with <= this matched fraction => omission
        "match_pct_warning": 85.0,
    },
    "silence": {"noise_db": -35, "min_duration": 0.25, "snap_window": 2.5},
    "storyboard": {
        "max_sentences_per_scene": 5,
        "max_items": 6,
        "max_words_per_item": 12,
        "max_title_words": 9,
    },
    "checks": {"math_trivial_numbers": [0, 1, 2]},
    "render": {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "quality": "looks",           # hyperframes: draft | looks | delivery
        "workers": "auto",
        "audio_bitrate": "192k",
        "burn_captions": True,
        "compressed_target_mb": 30,
        "compressed_audio_kbps": 64,
        "compressed_min_video_kbps": 120,
    },
    "captions": {"max_chars": 50, "font_size": 44, "min_duration": 0.8},
    "qa": {"frame_interval_s": 10, "sync_tolerance_s": 0.2},
    "gui": {"host": "127.0.0.1", "port": 8765},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: Path = CONFIG_PATH) -> dict:
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, data)


def save(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def update(patch: dict, path: Path = CONFIG_PATH) -> dict:
    cfg = _merge(load(path), patch)
    save(cfg, path)
    return cfg


def resolve(p: str | Path) -> Path:
    """Resolve a config path relative to the repository root."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
