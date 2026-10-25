# Start narravid (same as double-clicking "Make a video.cmd"). The browser opens by itself.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONIOENCODING = "utf-8"
# fully local: no telemetry, no update checks, no model hub access
$env:HYPERFRAMES_NO_TELEMETRY = "1"; $env:DO_NOT_TRACK = "1"; $env:HYPERFRAMES_NO_UPDATE_CHECK = "1"
$env:HF_HUB_OFFLINE = "1"; $env:HF_HUB_DISABLE_TELEMETRY = "1"
.\.venv\Scripts\python.exe -m narravid.server
