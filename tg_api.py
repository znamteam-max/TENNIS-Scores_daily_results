# tg_api.py
from __future__ import annotations
import os, json
import httpx

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BASE = f"https://api.telegram.org/bot{TOKEN}"

async def _post(method: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.post(f"{BASE}/{method}", json=payload)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": False, "raw": r.text}

async def send_message(chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = "Markdown"):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        return await _post("sendMessage", payload)
    except Exception as e:
        # тихо проглатываем, чтобы вебхук не падал
        print(f"[tg] send_message failed: {e}")
        return {"ok": False, "error": str(e)}

async def answer_callback_query(cq_id: str | None, text: str | None = None, show_alert: bool = False):
    if not cq_id:
        return {"ok": True}
    payload = {"callback_query_id": cq_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    try:
        return await _post("answerCallbackQuery", payload)
    except Exception as e:
        print(f"[tg] answer_callback_query failed: {e}")
        return {"ok": False, "error": str(e)}
