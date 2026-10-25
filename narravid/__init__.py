"""narravid — narration script + audio -> synchronised motion-graphic explainer video."""

import os as _os

__version__ = "0.1.0"

# Fully local at run time: models are loaded from models/whisper by path; never contact the Hub.
_os.environ.setdefault("HF_HUB_OFFLINE", "1")
_os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
