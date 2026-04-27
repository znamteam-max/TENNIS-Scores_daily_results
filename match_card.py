from __future__ import annotations

import os
import re
import urllib.request
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple


WIDTH = 1024
HEIGHT = 1280
LEFT_BAR_W = 62
BOTTOM_Y = 1018
PANEL = (21, 20, 25, 255)
WHITE = (246, 246, 246, 255)
GREEN = (216, 255, 48, 255)
LINE = (232, 232, 232, 235)

FONT_URLS = {
    "medium": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans%5Bwght%5D.ttf",
    "semibold_italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans-Italic%5Bwght%5D.ttf",
}


def _font_path(kind: str) -> str:
    local_names = {
        "medium": "SofiaSans-Medium.ttf",
        "semibold_italic": "SofiaSans-SemiBoldItalic.ttf",
    }
    here = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(here, "assets", "fonts", local_names[kind])
    if os.path.exists(local_path):
        return local_path

    cached = os.path.join("/tmp", os.path.basename(local_names[kind]))
    if not os.path.exists(cached):
        urllib.request.urlretrieve(FONT_URLS[kind], cached)
    return cached


def _font(kind: str, size: int):
    from PIL import ImageFont

    try:
        font = ImageFont.truetype(_font_path(kind), size)
        if hasattr(font, "set_variation_by_axes"):
            try:
                font.set_variation_by_axes([500 if kind == "medium" else 600])
            except Exception:
                pass
        return font
    except Exception:
        return ImageFont.load_default()


def _text_size(draw: Any, text: str, font: Any) -> Tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _fit_font(draw: Any, text: str, kind: str, size: int, max_width: int, min_size: int = 24):
    font = _font(kind, size)
    while size > min_size and _text_size(draw, text, font)[0] > max_width:
        size -= 2
        font = _font(kind, size)
    return font


def _draw_right_aligned(draw: Any, xy: Tuple[int, int], text: str, font: Any, fill: Tuple[int, int, int, int]) -> None:
    x, y = xy
    width, _ = _text_size(draw, text, font)
    draw.text((x - width, y), text, font=font, fill=fill)


def _surname(name: str) -> str:
    raw = " ".join(str(name or "").replace(",", " ").split())
    if not raw:
        return "TBD"
    if "/" in raw:
        return " / ".join(_surname(part) for part in raw.split("/"))
    parts = raw.split()
    if len(parts) > 1 and re.fullmatch(r"[A-ZА-ЯЁ]\.?",