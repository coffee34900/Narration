import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def cfg():
    from narravid import config
    return config.load()


@pytest.fixture
def fixtures():
    return FIX


def asr_from_text(text: str, t0: float = 0.0, step: float = 0.3) -> list[dict]:
    """Synthetic recogniser output: one word every `step` seconds."""
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + step * 0.8, 3), "p": 0.9})
        t += step
    return out
