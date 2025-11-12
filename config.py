import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()  # optional
TZ = os.getenv("TZ", "Europe/Helsinki")

# Polling interval (seconds) for checking match status / new results
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "75"))

# Data source: 'sofascore' (default). 'flashscore' is left as a placeholder.
DATA_SOURCE = os.getenv("DATA_SOURCE", "sofascore").lower()

# Webhook mode (optional): set these to enable webhook instead of long polling
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # e.g., https://your.app/bot/webhook
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

# HTTP timeouts
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10.0"))

# SQLite DB path
DB_PATH = os.getenv("DB_PATH", "bot.db")
