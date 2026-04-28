from __future__ import annotations

from typing import Any


def _patch_match_card() -> None:
    try:
        import match_card
    except Exception:
        return

    def _left_bar(img: Any, text: str) -> None:
        from PIL import Image, ImageDraw

        text = str(text or "").replace("\t", "    ").upper()
        font = match_card._font("medium", 28)
        probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tmp = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        d = ImageDraw.Draw(tmp)
        d.text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=match_card.WHITE)
        rotated = tmp.rotate(90, expand=True)
        x = max(0, match_card.LEFT_W - rotated.width - 12)
        img.alpha_composite(rotated, (x, 24))

    match_card._left_bar = _left_bar


_patch_match_card()
