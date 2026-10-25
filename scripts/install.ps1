# narravid installer for Windows (no admin needed). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# Needs network once; afterwards everything runs offline.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$NodeVersion = "v22.23.4"

Write-Host "== Python venv" -ForegroundColor Cyan
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "== ffmpeg" -ForegroundColor Cyan
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
$wg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ff -and -not $wg) { winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements }

Write-Host "== Portable Node.js $NodeVersion (tools\node)" -ForegroundColor Cyan
if (-not (Test-Path "tools\node\node.exe")) {
  New-Item -ItemType Directory -Force tools | Out-Null
  Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/$NodeVersion/node-$NodeVersion-win-x64.zip" -OutFile tools\node.zip
  Expand-Archive tools\node.zip -DestinationPath tools -Force
  Remove-Item tools\node.zip
  Rename-Item "tools\node-$NodeVersion-win-x64" node
}
$env:PATH = "$Root\tools\node;$env:PATH"

Write-Host "== HyperFrames + KaTeX + GSAP + Inter (renderer\)" -ForegroundColor Cyan
$env:HYPERFRAMES_NO_TELEMETRY = "1"; $env:DO_NOT_TRACK = "1"; $env:HYPERFRAMES_NO_UPDATE_CHECK = "1"
Push-Location renderer
npm ci
npx hyperframes telemetry disable      # persistent opt-out of HyperFrames' anonymous usage telemetry
npx hyperframes browser ensure
npx hyperframes doctor
Pop-Location

Write-Host "== Whisper models (models\whisper)" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -c "from faster_whisper import download_model; [download_model(m, output_dir='models/whisper/'+m) for m in ('base.en','small.en')]"

Write-Host "== Local LLM detection" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m narravid.pipeline models

Write-Host "`nDone. Start the GUI with: scripts\run.ps1" -ForegroundColor Green
