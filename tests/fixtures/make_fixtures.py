"""Synthesise test narration with the local Windows TTS (System.Speech / SAPI).

  sample.wav          the script read exactly
  sample_offscript.wav  with one inserted sentence and one repeated passage
                        (simulates the two TTS failures seen in real use)

Run:  python tests/fixtures/make_fixtures.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
INSERTED = "By the way, remember to subscribe to the channel for more videos like this one."


def speak(text: str, out: Path) -> None:
    txt = out.with_suffix(".tts.txt")
    txt.write_text(text, encoding="utf-8")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "try { $s.SelectVoice('Microsoft Zira Desktop') } catch {};"
        "$s.Rate = 1;"
        f"$s.SetOutputToWaveFile('{out}');"
        f"$s.Speak([IO.File]::ReadAllText('{txt}'));"
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    txt.unlink()


def spoken_text(script: str) -> str:
    # SAPI reads newlines as pauses; keep paragraph structure
    return script


def main() -> int:
    script = (HERE / "sample_script.txt").read_text(encoding="utf-8")
    speak(spoken_text(script), HERE / "sample.wav")
    lines = script.split("\n")
    # insertion after part two's second sentence, repetition of part three's first sentence
    off = script.replace("They reported a violation by more than five standard deviations.",
                         "They reported a violation by more than five standard deviations. " + INSERTED)
    rep = "The authors interpret the violation as evidence against local realism."
    off = off.replace(rep, rep + " " + rep)
    speak(off, HERE / "sample_offscript.wav")
    print("wrote", HERE / "sample.wav", HERE / "sample_offscript.wav", len(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
