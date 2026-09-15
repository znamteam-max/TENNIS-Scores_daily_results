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
PHOTO_LEFT = 65
PHOTO_TOP = 0
PHOTO_RIGHT = 1080
PHOTO_BOTTOM = PANEL_TOP
BACKGROUND_FULL = "result_background_full.png"
LEGACY_BACKGROUND = "result_background.jpg"


def _patch_match_card() -> None:
    try:
        import match_card
    except Exception:
        return

    # Approved result-card geometry from the September 2026 reference.
    match_card.BOTTOM_Y = PANEL_TOP
    match_card.PANEL = (20, 20, 25, 255)
    match_card.WHITE = (255, 255, 255, 255)
    match_card.LINE = (255, 255, 255, 255)
    match_card.ROW1_CENTER_Y = 1097
    match_card.ROW2_CENTER_Y = 1207

    # Keep card rendering alive on a cold Vercel function even if the remote
    # Sofia Sans download is temporarily unavailable.
    original_font = match_card._font

    @lru_cache(maxsize=128)
    def _safe_font(kind: str, size: int):
        try:
            return original_font(kind, size)
        except Exception as exc:
            print(f"[card] primary font load failed kind={kind} size={size}: {exc}")
            from PIL import ImageFont

            if kind == "medium":
                candidates = (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "DejaVuSans.ttf",
                )
            else:
                candidates = (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-BoldOblique.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
                    "DejaVuSans-BoldOblique.ttf",
                    "DejaVuSans.ttf",
                )
            for path in candidates:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
            return ImageFont.load_default()

    match_card._font = _safe_font

    @lru_cache(maxsize=1)
    def _background_full():
        """Load the designer-supplied 1080x1350 background without recreating it."""
        from PIL import Image

        preferred = os.path.join(match_card.TEMPLATE_DIR, BACKGROUND_FULL)
        legacy = os.path.join(match_card.TEMPLATE_DIR, LEGACY_BACKGROUND)
        path = preferred if os.path.exists(preferred) else legacy
        if path == legacy:
            print(
                f"[card] WARNING {BACKGROUND_FULL} is missing; "
                f"temporarily using {LEGACY_BACKGROUND}"
            )

        image = Image.open(path)
        image.load()
        image = image.convert("RGBA")
        if image.size != (match_card.W, match_card.H):
            raise ValueError(
                f"result background has wrong size {image.size}; "
                f"expected {(match_card.W, match_card.H)}"
            )
        return image

    def _base_template(count: int):
        """
        Build the frame from the supplied full background.

        The player-photo area is physically cut out (transparent), exactly as
        in the previous templates. Everything that remains visible around it
        is therefore taken from the supplied background image itself: the full
        left strip and the bottom strip below the raised score panel.
        """
        from PIL import ImageDraw

        image = _background_full().copy()
        draw = ImageDraw.Draw(image)

        # Real transparent hole for the player photo: x=65..1079, y=0..1009.
        # match_card._overlay_photo() later fills exactly this 1015x1010 area.
        draw.rectangle(
            (PHOTO_LEFT, PHOTO_TOP, PHOTO_RIGHT - 1, PHOTO_BOTTOM - 1),
            fill=(0, 0, 0, 0),
        )

        # Raised score panel. The supplied background remains untouched on the
        # left and again below PANEL_BOTTOM, matching the approved reference.
        draw.rectangle(
            (match_card.LEFT_W, PANEL_TOP, match_card.W - 1, PANEL_BOTTOM),
            fill=match_card.PANEL,
        )
        draw.rectangle(
            (SEPARATOR_LEFT, SEPARATOR_Y, SEPARATOR_RIGHT, SEPARATOR_Y + 1),
            fill=match_card.WHITE,
        )

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
