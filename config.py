import os
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10.0"))
TZ = os.getenv("APP_TZ") or os.getenv("TZ") or "Europe/Helsinki"
DATA_SOURCE = os.getenv("DATA_SOURCE", "sofascore").lower()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("POSTGRES_DATABASE_URL") or os.getenv("POSTGRES_PRISMA_URL")
