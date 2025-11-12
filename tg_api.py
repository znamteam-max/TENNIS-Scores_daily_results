
import os, httpx

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text})
        r.raise_for_status()
        return r.json()

async def set_webhook(url: str, secret_token: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{API}/setWebhook", json={"url": url, "secret_token": secret_token})
        r.raise_for_status()
        return r.json()
