# narravid

Turn a narration recording into an explainer video. Everything runs on this computer.

## Make a video

1. Double-click **`Make a video.cmd`**. Your browser opens.
2. Drop in your **recording** (WAV / MP3 / M4A). The script is optional.
3. Press **Make my video**.

That's it. No questions. You'll hear a chime when it's done. The video and its captions are saved in
the **`outputs`** folder next to `Make a video.cmd` (for example `outputs\my-talk.mp4` and
`outputs\my-talk.srt`).

Keep the black window open while you work. Close it to quit. Starting it twice just reopens the tab.

### Optional: add the script
The app writes the text from the recording itself. Adding the script (link under the button)
only gives exact spelling of names and symbols. Script format: plain text. Start each section with a line like `Part one. My section title`, then write normal
sentences. Abbreviations ("e.g.", "F. M. Scherer") and numbers ("0.08", "450k") are handled.

### Options
Under **More options** there is one switch: **Smart visuals** (default on) uses the local AI to choose
charts and key numbers. Turn it off for plain text slides that are ready much sooner.

## What happens automatically
* **Listens to the recording**: speech recognition writes out the narration (if no script was given)
  and finds when each word is spoken, so every slide and caption appears in sync. The recording is
  used exactly as it is, never checked or cut.
* **Finds the parts**: spoken headings like "Part one. Title." become part title cards.
* **Draws the concept as motion graphics**: the local AI describes each idea as parts and relations
  (no code); the app lays it out as an animated schematic — icons that draw themselves, arrows that grow,
  particles flowing along "flows", the part being discussed enlarged while the rest dims, a slow camera
  push and a drifting background. Layouts: chain, cycle, hub, versus, layers, tree. Numbers count up.
  Where no drawing fits, the narration itself appears as kinetic text, each word lighting up as it is spoken.
* **Checks every fact**: each number, name and formula on screen must come from the narration. If a label
  fails, only that label (or node) is removed; if a whole scene fails, it becomes kinetic text of the
  narration, so nothing made up ever appears.
* **Checks the layout**: crowded slides are simplified so text never overlaps the captions.
* **Nothing is kept** except the finished video and captions. Work files are deleted after each video.
* **Fully local**: no cloud APIs. The AI is your local model (Ollama / LM Studio); the app refuses any
  non-local AI address, and the renderer's telemetry is switched off.

## First-time setup
`Make a video.cmd` runs the installer automatically the first time (it needs internet once):
Python packages, ffmpeg, a portable Node.js, the HyperFrames renderer, KaTeX, fonts and the speech model.
You also need a local AI model in Ollama for smart visuals (for example `ollama pull qwen3.5:4b`).
Without one, the app still works and makes plain text slides.

To reinstall manually: `powershell -ExecutionPolicy Bypass -File scripts\install.ps1`

## If something goes wrong
* **"Port already in use"**: fixed. A second start now just opens the existing tab.
* **"The audio file could not be read"**: export the recording as WAV or MP3.
* **It's slow**: a 30-minute narration takes roughly 35-45 minutes with smart visuals on this laptop;
  most of that is building the video. Untick *Smart visuals* for a faster plain version.
* **GPU**: speech recognition and the local AI run on the NVIDIA GPU (the AI fully, `llm.num_gpu: 99`).
  If the GPU is unavailable or out of memory, both fall back to the CPU automatically.
* **The renderer had a problem**: long narrations are now rendered in pieces of about 90 seconds
  (`render.chunk_max_seconds`) and a crashed piece is retried once automatically. If it still fails,
  lower `chunk_max_seconds` in `config.yaml` (e.g. 45); `scripts\install.ps1` only helps if files are missing.

## For developers
Command line: `.venv\Scripts\python -m narravid.make audio.wav [--script script.txt] [--simple]`.
Tests: `.venv\Scripts\python -m pytest` (fast) and `... -m slow -o addopts=""` (real audio + full videos).
Code: `narravid/make.py` runs the whole flow; the stages it calls are in `narravid/pipeline.py`
(ingest → align → verify → storyboard → checks → compose → render), and `narravid/server.py` + `static/index.html`
are the web app.
