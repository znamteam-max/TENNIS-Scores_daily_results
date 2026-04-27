from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from match_card import build_match_card_png
from providers import sofascore as ss


def _api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _post_json(url: str, payload: Dict[str, Any]) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = "<body read failed>"
        print(f"[tg] request failed: status={exc.code} body={body}")
    except urllib.error.URLError as exc:
        print(f"[tg] request failed: {exc}")
    return False


def _post_multipart(
    url: str,
    fields: Dict[str, Any],
    file_field: str,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> bool:
    boundary = f"----tennis-card-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        if value is None:
            continue
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    file_header = (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("ascii")
    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    chunks.append(file_header)
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))

    body = b"".join(chunks)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")
        except Exception:
            body_text = "<body read failed>"
        print(f"[tg] photo failed: status={exc.code} body={body_text}")
    except urllib.error.URLError as exc:
        print(f"[tg] photo failed: {exc}")
    return False


def send_match_result(bot_token: str, chat_id: int, event: Dict[str, Any]) -> bool:
    if not bot_token:
        return False

    text = ss.result_message(event)
    try:
        png = build_match_card_png(event)
        caption: Optional[str] = text if len(text) <= 1000 else None
        photo_ok = _post_multipart(
            _api_url(bot_token, "sendPhoto"),
            {"chat_id": chat_id, "caption": caption},
            "photo",
            "match-result.png",
            "image/png",
            png,
        )
        if photo_ok:
            if caption is None:
                return _post_json(_api_url(bot_token, "sendMessage"), {"chat_id": chat_id, "text": text})
            return True
    except Exception as exc:
        print(f"[card] render/send failed: {exc}")

    return _post_json(_api_url(bot_token, "sendMessage"), {"chat_id": chat_id, "text": text})
