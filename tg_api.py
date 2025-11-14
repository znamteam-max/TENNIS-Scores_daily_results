import os, httpx

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None

async def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    if not API:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{API}/sendMessage", json=payload)
        r.raise_for_status()
        return r.json()

async def answer_callback_query(callback_query_id: str, text: str | None = None, show_alert: bool = False):
    if not API:
        return
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(f"{API}/answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert
        })
