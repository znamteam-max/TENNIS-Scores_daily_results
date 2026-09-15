from __future__ import annotations

from io import BytesIO
from typing import Any


_INSTALLED = False


def _jpeg_fallback(png_bytes: bytes) -> bytes:
    from PIL import Image

    image = Image.open(BytesIO(png_bytes)).convert("RGB")
    for quality in (92, 88, 84, 80):
        out = BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = out.getvalue()
        if len(data) <= 9_000_000:
            return data
    return data


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        import telegram_media
    except Exception as exc:
        print(f"[card] delivery patch import failed: {exc}")
        return

    original_post_multipart = telegram_media._post_multipart

    def post_multipart(
        url: str,
        fields: dict[str, Any],
        file_field: str,
        filename: str,
        content_type: str,
        file_bytes: bytes,
    ):
        response = original_post_multipart(
            url,
            fields,
            file_field,
            filename,
            content_type,
            file_bytes,
        )
        if response and response.get("ok", True):
            return response

        # Telegram occasionally rejects/times out on sendDocument even though
        # the same 1080x1350 card is a valid photo. Retry transparently as a
        # compressed JPEG photo before reporting failure to the editor.
        if url.endswith("/sendDocument"):
            try:
                jpeg = _jpeg_fallback(file_bytes)
                photo_url = url[: -len("sendDocument")] + "sendPhoto"
                print(
                    "[card] sendDocument failed; retrying sendPhoto "
                    f"png_bytes={len(file_bytes)} jpeg_bytes={len(jpeg)}"
                )
                retry = original_post_multipart(
                    photo_url,
                    fields,
                    "photo",
                    "match-result.jpg",
                    "image/jpeg",
                    jpeg,
                )
                if retry and retry.get("ok", True):
                    return retry
                print(f"[card] sendPhoto fallback failed response={retry}")
            except Exception as exc:
                print(f"[card] sendPhoto fallback raised: {exc}")
        return response

    telegram_media._post_multipart = post_multipart
    _INSTALLED = True
