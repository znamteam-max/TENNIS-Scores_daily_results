import os, httpx
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None
async def send_message(chat_id: int, text: str):
    if not API:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text})
        r.raise_for_status()
        return r.json()
