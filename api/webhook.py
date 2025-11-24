# api/webhook.py — минимальный WSGI вебхук для Telegram без внешних зависимостей
import os, sys, json
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

def _json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except Exception:
        return b'{"ok":false}'

def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _tg_send(chat_id: int, text: str):
    if not BOT_TOKEN:
        return
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = Request(_tg_api("sendMessage"), data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as r:
            r.read()  # игнорируем тело
    except (URLError, HTTPError):
        pass

def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "")

    # GET — просто жив
    if method == "GET":
        body = _json({"ok": True, "service": "webhook", "path": "/api/webhook"})
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"),
                                  ("Content-Length", str(len(body)))])
        return [body]

    # POST — проверяем секрет, если задан
    if method == "POST" and WEBHOOK_SECRET:
        if environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN") != WEBHOOK_SECRET:
            body = _json({"error": "forbidden"})
            start_response("403 Forbidden", [("Content-Type", "application/json; charset=utf-8"),
                                             ("Content-Length", str(len(body)))])
            return [body]

    # читаем JSON-апдейт
    try:
        clen = int(environ.get("CONTENT_LENGTH") or "0")
    except Exception:
        clen = 0
    raw = b""
    if clen > 0 and environ.get("wsgi.input"):
        raw = environ["wsgi.input"].read(clen)
    try:
        update = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        update = {}

    # callback_query просто подтверждаем
    cb = update.get("callback_query")
    if cb and isinstance(cb, dict):
        # можно добавить answerCallbackQuery так же через urllib, если нужно
        body = _json({"ok": True, "cb": True})
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"),
                                  ("Content-Length", str(len(body)))])
        return [body]

    # обычное сообщение
    msg = update.get("message") or update.get("edited_message")
    if isinstance(msg, dict):
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text") or ""
        if isinstance(chat_id, int):
            if text.strip().lower().startswith("/ping"):
                _tg_send(chat_id, "pong ✅")
            elif text.strip().lower().startswith("/start"):
                _tg_send(chat_id, "Бот жив ✅\nЭто упрощённый режим без БД. Напишите /ping для проверки ответа.")
            # здесь можно добавить ещё простые тест-команды

    body = _json({"ok": True})
    start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"),
                              ("Content-Length", str(len(body)))])
    return [body]
