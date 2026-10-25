"""Locate external binaries (ffmpeg, ffprobe, node, hyperframes) and run them."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from . import config


class ToolMissing(RuntimeError):
    pass


def _winget_ffmpeg(name: str) -> str | None:
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    hits = sorted(glob.glob(os.path.join(base, "Gyan.FFmpeg*", "*", "bin", name + ".exe")))
    return hits[-1] if hits else None


@lru_cache(maxsize=None)
def find(name: str) -> str:
    cfg = config.load()
    configured = cfg["paths"].get(name, "")
    if configured and Path(configured).exists():
        return configured
    local = {
        "node": config.ROOT / "tools" / "node" / "node.exe",
        "ffmpeg": config.ROOT / "tools" / "ffmpeg" / "ffmpeg.exe",
        "ffprobe": config.ROOT / "tools" / "ffmpeg" / "ffprobe.exe",
    }.get(name)
    if local and local.exists():
        return str(local)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    if name in ("ffmpeg", "ffprobe"):
        w = _winget_ffmpeg(name)
        if w:
            return w
    hints = {
        "ffmpeg": "Install with: winget install Gyan.FFmpeg  (or set paths.ffmpeg in config.yaml)",
        "ffprobe": "Installed together with ffmpeg (winget install Gyan.FFmpeg)",
        "node": "Run scripts/install.ps1, which downloads portable Node 22 into tools/node",
    }
    raise ToolMissing(f"{name} not found. {hints.get(name, '')}")


def renderer_dir() -> Path:
    return config.resolve(config.load()["paths"]["renderer_dir"])


# Everything runs offline: HyperFrames' anonymous PostHog telemetry, update check and
# auto-install are switched off for every Node process we start.
OFFLINE_ENV = {
    "HYPERFRAMES_NO_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "HYPERFRAMES_NO_UPDATE_CHECK": "1",
    "HYPERFRAMES_NO_AUTO_INSTALL": "1",
    "NO_UPDATE_NOTIFIER": "1",
    "npm_config_update_notifier": "false",
}


def node_env() -> dict:
    env = os.environ.copy()
    node = Path(find("node")).parent
    ff = Path(find("ffmpeg")).parent
    env["PATH"] = f"{node}{os.pathsep}{ff}{os.pathsep}" + env.get("PATH", "")
    env.update(OFFLINE_ENV)
    return env


def hyperframes_cmd() -> list[str]:
    cli = renderer_dir() / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    if not cli.exists():
        raise ToolMissing("HyperFrames is not installed. Run scripts/install.ps1 (npm install in renderer/).")
    return [find("node"), str(cli)]


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, log=None,
        check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    if log:
        log(" ".join(str(c) for c in cmd)[:400])
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if check and p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-3000:]
        raise RuntimeError(f"Command failed ({p.returncode}): {Path(str(cmd[0])).name} ...\n{tail}")
    return p


def run_streaming(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, log=None) -> int:
    """Run a long command, forwarding its output lines to `log`."""
    if log:
        log(" ".join(str(c) for c in cmd)[:400])
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    assert p.stdout
    for line in p.stdout:
        line = line.rstrip()
        if line and log and not line.startswith("[INFO] [Render:trace]"):
            log(line)
    return p.wait()


def ffprobe_duration(path: Path) -> float:
    p = run([find("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(p.stdout)["format"]["duration"])


def ffprobe_streams(path: Path) -> list[dict]:
    p = run([find("ffprobe"), "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,duration,nb_frames,bit_rate",
             "-of", "json", str(path)])
    return json.loads(p.stdout).get("streams", [])


def node_script(script: str, payload: dict, *, timeout: float = 600) -> dict:
    """Run one of our bundled Node helpers (narravid/js/*.mjs) with JSON on stdin."""
    js = Path(__file__).parent / "js" / script
    env = node_env()
    env["NODE_PATH"] = str(renderer_dir() / "node_modules")
    p = subprocess.run([find("node"), str(js)], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", env=env, cwd=renderer_dir(), timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{script} failed:\n{p.stderr[-3000:]}")
    return json.loads(p.stdout)
