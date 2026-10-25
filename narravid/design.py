"""Design system tokens — one source of truth for CSS, captions and the contrast test.

Geometry (1920x1080):
  safe area     x 96..1824, y 40..880
  caption zone  y >= 880 (burned-in captions live here; nothing else may enter it)
  content box   x 120..1800, y 150..850
"""
from __future__ import annotations

W, H = 1920, 1080
SAFE = {"left": 96, "right": 1824, "top": 40, "bottom": 880}
CAPTION_TOP = 880

PALETTE = {
    "bg": "#0B1020",
    "surface": "#141B2E",
    "surface2": "#1C2541",
    "line": "#33405F",
    "line-strong": "#7D8AA8",
    "text": "#E9EDF5",
    "muted": "#AEB8CC",
    "accent": "#7FE0C2",
    # semantic roles
    "measured": "#5CC8FF",
    "interpretation": "#FFB547",
    "commentary": "#C9AEFF",
    "warning": "#FF8A80",
    "schematic": "#FFD166",
}
ROLE_LABELS = {
    "measured": "Measured result",
    "interpretation": "Authors' interpretation",
    "commentary": "Commentary",
    "warning": "Caveat",
}
SERIES = ["measured", "interpretation", "commentary", "accent", "warning", "schematic"]

TYPE = {"font": "Inter", "min_px": 28, "body_px": 38, "title_px": 56, "header_px": 28}

MOTION = {"scene_in": 0.5, "scene_out": 0.4, "item_in": 0.6, "item_rise": 18, "min_item_gap": 0.6,
          "min_visible": 1.2}


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def css_vars() -> str:
    return ":root{" + "".join(f"--{k}:{v};" for k, v in PALETTE.items()) + "}"
