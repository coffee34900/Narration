"""The web app: one screen, one button. Start with  python -m narravid.server

If the app is already running, starting it again just opens the browser tab.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.request
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import config, make

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="narravid")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

JOB: make.Job | None = None
_uploads: Path | None = None
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac")


@app.get("/", response_class=HTMLResponse)
def index():
    # never cache: an old open tab must not keep showing a previous version of the app
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/ping")
def ping():
    return {"app": "narravid"}


@app.get("/api/status")
def status():
    return JOB.view() if JOB else {"status": "idle"}


@app.post("/api/start")
async def start(audio: UploadFile = File(...), script: UploadFile | None = File(None), smart: str = Form("1")):
    """Only the recording is required; a script is optional (for exact spelling)."""
    global JOB, _uploads
    if JOB and JOB.status == "running":
        raise HTTPException(409, "A video is already being made. Wait for it, or press Stop.")
    a_ext = Path(audio.filename or "").suffix.lower()
    if a_ext not in AUDIO_EXT:
        raise HTTPException(400, "The recording should be a WAV, MP3 or M4A file.")
    if _uploads:
        shutil.rmtree(_uploads, ignore_errors=True)
    _uploads = Path(tempfile.mkdtemp(prefix=make.UPLOAD_PREFIX))
    ap = _uploads / f"audio{a_ext}"
    ap.write_bytes(await audio.read())
    sp = None
    if script is not None and script.filename:
        s_ext = Path(script.filename).suffix.lower()
        if s_ext not in (".txt", ".md", ""):
            raise HTTPException(400, "The script should be a .txt text file.")
        sp = _uploads / f"script{s_ext or '.txt'}"
        sp.write_bytes(await script.read())
        if not sp.read_text(encoding="utf-8-sig", errors="replace").strip():
            sp = None                       # empty script: just use the recording
    JOB = make.Job((script.filename if sp else None) or audio.filename or "video")
    job = JOB
    folder = _uploads

    def go():
        make.run(job, ap, sp, smart=smart == "1")
        shutil.rmtree(folder, ignore_errors=True)

    threading.Thread(target=go, daemon=True).start()
    return job.view()


@app.post("/api/stop")
def stop():
    if JOB:
        JOB.cancel()
    return {"ok": True}


@app.get("/api/result/{kind}")
def result(kind: str, download: int = 0):
    if not JOB or not JOB.result:
        raise HTTPException(404)
    f = Path({"video": JOB.result["video"], "srt": JOB.result["srt"]}.get(kind, ""))
    if not f.is_file():
        raise HTTPException(404, "The file was moved or deleted")
    return FileResponse(f, filename=f.name if download else None,
                        media_type="video/mp4" if kind == "video" else "text/plain")


@app.post("/api/show-folder")
def show_folder():
    target = Path(JOB.result["video"]) if JOB and JOB.result else make.OUTPUT_DIR
    make.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        subprocess.run(["explorer", f"/select,{target}"], check=False)
    else:
        os.startfile(str(make.OUTPUT_DIR))  # noqa: S606
    return {"ok": True}


# ---------------------------------------------------------------- start-up
def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _is_ours(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/ping", timeout=1.5) as r:
            return b"narravid" in r.read()
    except Exception:  # noqa: BLE001
        return False


def main():
    import uvicorn
    cfg = config.load()
    host, port = cfg["gui"]["host"], int(cfg["gui"]["port"])
    if not _port_free(host, port):
        if _is_ours(host, port):
            print(f"narravid is already running — opening http://{host}:{port}")
            if not os.environ.get("NARRAVID_NO_BROWSER"):
                webbrowser.open(f"http://{host}:{port}")
            return
        port = next(p for p in range(port + 1, port + 50) if _port_free(host, p))
    make.cleanup_old_jobs()
    make.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    url = f"http://{host}:{port}"
    print(f"\n  narravid is running at {url}\n  Keep this window open while you make videos. Close it to quit.\n")
    if not os.environ.get("NARRAVID_NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
