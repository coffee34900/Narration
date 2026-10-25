"""Speech recognisers with word timestamps. Engine chosen by config asr.engine."""
from __future__ import annotations

from pathlib import Path

from . import config


def transcribe(wav: Path, cfg: dict, log=print) -> list[dict]:
    """Return [{"w": text, "s": start, "e": end, "p": probability}] for the whole file."""
    engine = cfg["asr"]["engine"]
    if engine == "faster-whisper":
        return _faster_whisper(wav, cfg["asr"], log)
    if engine == "pocketsphinx":
        return _pocketsphinx(wav, cfg["asr"], log)
    raise ValueError(f"Unknown ASR engine '{engine}' (use faster-whisper or pocketsphinx)")


def _model_path(acfg: dict) -> str:
    local = config.resolve(acfg["model_dir"]) / acfg["model"]
    if (local / "model.bin").exists():
        return str(local)
    raise FileNotFoundError(
        f"Whisper model '{acfg['model']}' not found in {local}. Run scripts/install.ps1 "
        "(it downloads base.en and small.en), or set asr.model to one that exists.")


def _read_wav16k(wav: Path):
    """Our analysis WAV is 16 kHz mono PCM16; decoding it ourselves avoids a PyAV API
    incompatibility (faster-whisper 1.2.1 passes metadata_errors=, removed in PyAV 19)."""
    import wave

    import numpy as np
    with wave.open(str(wav), "rb") as fh:
        if fh.getframerate() != 16000 or fh.getnchannels() != 1 or fh.getsampwidth() != 2:
            raise ValueError(f"{wav} must be 16 kHz mono PCM16 (re-run Ingest)")
        data = fh.readframes(fh.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


# One model per (path, device, type) for the life of the process. Re-creating a
# WhisperModel after a failed CUDA attempt deadlocked ctranslate2 in a long-lived
# process (GUI server, test session), so models are cached and a CUDA failure is remembered.
_MODELS: dict[tuple, object] = {}
_CUDA_BROKEN = False


def _register_cuda_dlls() -> None:
    """Make the pip-installed NVIDIA runtime (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable.

    Windows does not search site-packages for DLLs, and ctranslate2 loads cuBLAS/cuDNN by
    name, so their bin folders go on both the DLL search path and PATH.
    """
    import os
    import sys
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for bin_dir in sorted(base.glob("*/bin")):
        try:
            os.add_dll_directory(str(bin_dir))
        except (OSError, AttributeError):
            pass
        if str(bin_dir) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _faster_whisper(wav: Path, acfg: dict, log) -> list[dict]:
    global _CUDA_BROKEN
    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    samples = _read_wav16k(wav)
    path = _model_path(acfg)
    device = acfg.get("device", "auto")
    attempts = [("cuda", "float16"), ("cpu", "int8")] if device == "auto" else \
        [(device, acfg.get("compute_type") if acfg.get("compute_type") != "auto" else
          ("float16" if device == "cuda" else "int8"))]
    if _CUDA_BROKEN and device == "auto":
        attempts = [a for a in attempts if a[0] != "cuda"]
    last_err = None
    for dev, ctype in attempts:
        try:
            key = (path, dev, ctype)
            model = _MODELS.get(key)
            if model is None:
                model = WhisperModel(path, device=dev, compute_type=ctype, cpu_threads=0)
                _MODELS[key] = model
            elif not model.model.model_is_loaded:
                model.model.load_model()           # weights were released after the last run
            log(f"ASR: faster-whisper {acfg['model']} on {dev}/{ctype}")
            segments, info = model.transcribe(
                samples, language=acfg.get("language") or None, beam_size=int(acfg.get("beam_size", 5)),
                word_timestamps=True, vad_filter=False, condition_on_previous_text=False)
            words = []
            last_report = 0.0
            for seg in segments:          # generator: errors (e.g. missing cuDNN) surface here
                for w in seg.words or []:
                    raw = w.word.replace("�", "")
                    # "r" keeps Whisper's own spacing: ".83" in "2.83" has no leading space
                    words.append({"w": raw.strip(), "r": raw, "s": round(float(w.start), 3),
                                  "e": round(float(w.end), 3), "p": round(float(w.probability), 3)})
                if seg.end - last_report > 60:
                    last_report = seg.end
                    log(f"ASR: {seg.end / 60:.1f} / {info.duration / 60:.1f} min")
            if dev == "cuda":
                # free the 4 GB card for the local LLM (Ollama) that runs next; the model
                # object is kept so it can be reloaded without being re-created
                model.model.unload_model()
            return [w for w in words if w["w"]]
        except Exception as e:  # noqa: BLE001 — try the next device
            last_err = e
            _MODELS.pop((path, dev, ctype), None)
            if dev == "cuda":
                _CUDA_BROKEN = True
            log(f"ASR on {dev} failed ({type(e).__name__}: {str(e)[:160]}); trying next option")
    raise RuntimeError(f"faster-whisper could not run: {last_err}")


def _pocketsphinx(wav: Path, acfg: dict, log) -> list[dict]:
    try:
        from pocketsphinx import Decoder, Config  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "PocketSphinx is not installed (no wheel for this Python version; building it needs "
            "MSVC Build Tools). Use asr.engine: faster-whisper.") from e
    import wave
    cfgp = Config()
    dec = Decoder(cfgp)
    with wave.open(str(wav), "rb") as fh:
        data = fh.readframes(fh.getnframes())
    dec.start_utt()
    dec.process_raw(data, full_utt=True)
    dec.end_utt()
    fr = 0.01
    words = []
    for seg in dec.seg():
        if seg.word.startswith("<") or seg.word.startswith("["):
            continue
        words.append({"w": seg.word.split("(")[0], "s": seg.start_frame * fr, "e": seg.end_frame * fr, "p": 1.0})
    log(f"ASR: pocketsphinx, {len(words)} words")
    return words
