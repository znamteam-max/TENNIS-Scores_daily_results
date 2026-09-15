from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


PANEL_TOP = 1010
PANEL_BOTTOM = 1285
SEPARATOR_LEFT = 126
SEPARATOR_RIGHT = 1048
SEPARATOR_Y = 1154
VERTICAL_TOP = 1065
VERTICAL_BOTTOM = 1239


def _patch_match_card() -> None:
    try:
        import match_card
    except Exception:
        return

    # Approved result-card geometry from the September 2026 reference.
    # The score panel is 64 px higher than the previous layout so social-app
    # overlays do not sit on top of the score.
    match_card.BOTTOM_Y = PANEL_TOP
    match_card.PANEL = (20, 20, 25, 255)
    match_card.WHITE = (255, 255, 255, 255)
    match_card.LINE = (255, 255, 255, 255)
    match_card.ROW1_CENTER_Y = 1097
    match_card.ROW2_CENTER_Y = 1207

    @lru_cache(maxsize=1)
    def _background():
        from PIL import Image

        path = os.path.join(match_card.TEMPLATE_DIR, "result_background.jpg")
        if not os.path.exists(path):
            raise FileNotFoundError(f"result background not found: {path}")
        image = Image.open(path).convert("RGBA")
        if image.size != (match_card.W, match_card.H):
            image = image.resize(
                (match_card.W, match_card.H),
                Image.Resampling.LANCZOS,
            )
        return image

    def _base_template(count: int):
        from PIL import ImageDraw

        image = _background().copy()
        draw = ImageDraw.Draw(image)

        # Score block.
        draw.rectangle(
            (match_card.LEFT_W, PANEL_TOP, match_card.W - 1, PANEL_BOTTOM),
            fill=match_card.PANEL,
        )
        draw.rectangle(
            (SEPARATOR_LEFT, SEPARATOR_Y, SEPARATOR_RIGHT, SEPARATOR_Y + 1),
            fill=match_card.WHITE,
        )

        # Score-column separators follow the same score centers as the
        # dynamic text, so 2/3/4/5-set cards stay aligned.
        template_key = min(6, max(3, int(count)))
        centers = match_card._score_centers(template_key)
        for left, right in zip(centers, centers[1:]):
            x = (left + right) // 2
            draw.rectangle(
                (x, VERTICAL_TOP, x + 1, VERTICAL_BOTTOM),
                fill=match_card.WHITE,
            )
        return image

    def _left_bar(img: Any, text: str) -> None:
        from PIL import Image, ImageDraw

        text = str(text or "").replace("\t", "    ").upper()
        font = match_card._font("medium", 28)
        probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tmp = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tmp)
        draw.text(
            (4 - bbox[0], 4 - bbox[1]),
            text,
            font=font,
            fill=match_card.WHITE,
        )
        rotated = tmp.rotate(90, expand=True)
        x = max(0, match_card.LEFT_W - rotated.width - 12)
        img.alpha_composite(rotated, (x, 24))

    match_card._base_template = _base_template
    match_card._left_bar = _left_bar


_patch_match_card()
