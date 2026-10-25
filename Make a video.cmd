@echo off
rem Double-click to start. Your browser opens by itself. Keep this window open; close it to quit.
title narravid - keep this window open
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set HYPERFRAMES_NO_TELEMETRY=1
set DO_NOT_TRACK=1
set HYPERFRAMES_NO_UPDATE_CHECK=1
set HF_HUB_OFFLINE=1
set HF_HUB_DISABLE_TELEMETRY=1
if not exist ".venv\Scripts\python.exe" (
  echo narravid is not installed yet. Running the installer once...
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install.ps1"
)
".venv\Scripts\python.exe" -m narravid.server
if errorlevel 1 pause
