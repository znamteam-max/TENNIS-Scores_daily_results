# /api/webhook.py
from __future__ import annotations
import os, json, datetime as dt
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo
import httpx

from db_pg import (
    ensure_schema,
    get_tz, set_tz,
    add_watch, remove_watch, list_today,
    ru_name_for, set_alias,
    set_pending_alias, consume_pending_alias,
    get_events_cache,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/London")

def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

async def tg_send_message(chat_id: int, text: str, **kwargs) -> None:
    if not BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text}
    payload.update(kwargs)
    async with httpx.AsyncClient(timeout=15.0) as c:
        await c.post(_tg_api("sendMessage"), json=payload)

async def tg_answer_callback_query(cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
    if not BOT_TOKEN:
        return
    payload = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    async with httpx.AsyncClient(timeout=15.0) as c:
        await c.post(_tg_api("answerCallbackQuery"), json=payload)

def _tz_for(chat_id: int) -> ZoneInfo:
    try:
        return ZoneInfo(get_tz(chat_id) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)

def _today(chat_id: int) -> dt.date:
    return dt.datetime.now(_tz_for(chat_id)).date()

# автодополнение EN -> полное EN (короткие токены)
KNOWN_EN = {
    "sinner": "Jannik Sinner",
    "zverev": "Alexander Zverev",
    "rublev": "Andrey Rublev",
    "medvedev": "Daniil Medvedev",
    "djokovic": "Novak Djokovic",
    "alcaraz": "Carlos Alcaraz",
    "musetti": "Lorenzo Musetti",
    "de minaur": "Alex de Minaur",
    "deminour": "Alex de Minaur",
    "tsitsipas": "Stefanos Tsitsipas",
    "wawrinka": "Stan Wawrinka",
}

def _canon_en(token: str) -> str:
    t = (token or "").strip()
    k = " ".join(t.lower().split())
    return KNOWN_EN.get(k, t)

def _auto_ru_guess(en_full: str) -> str:
    m = {
        "Jannik Sinner": "Янник Синнер",
        "Alexander Zverev": "Александр Зверев",
        "Andrey Rublev": "Андрей Рублёв",
        "Daniil Medvedev": "Даниил Медведев",
        "Novak Djokovic": "Новак Джокович",
        "Carlos Alcaraz": "Карлос Алькарас",
        "Lorenzo Musetti": "Лоренцо Музетти",
        "Alex de Minaur": "Алекс де Минор",
        "Stefanos Tsitsipas": "Стефанос Циципас",
        "Stan Wawrinka": "Стан Вавринка",
    }
    return m.get(en_full, en_full)

def _format_list_with_ru(items: List[str]) -> str:
    lines = []
    for it in items:
        ru, known = ru_name_for(it)
        lines.append(f"• {ru if (ru and known) else it}")
    return "\n".join(lines) if lines else "—"

async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send_message(chat_id, f"Сегодня ({day.isoformat()}):\n—\n\nДобавьте игроков: /watch Rublev, Musetti")
        return
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send_message(
        chat_id,
        "Сегодня (%s):\n%s\n\nХотите исправить список?" % (day.isoformat(), _format_list_with_ru(arr)),
        reply_markup={"inline_keyboard": buttons}
    )

async def _send_start(chat_id: int):
    ds = _today(chat_id)
    data = get_events_cache(ds)
    if not data:
        await tg_send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\nКэш пополнится GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: /watch Rublev, Musetti."
        )
        return

    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []

    if not events:
        await tg_send_message(chat_id, "На сегодня список пуст. /watch Имя — чтобы отслеживать.")
        return

    # грубая группировка по турнирам
    by_t: Dict[str, int] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        tname = None
        t = ev.get("tournament") or {}
        ut = t.get("uniqueTournament") or {}
        tname = ut.get("name") or t.get("name") or "Турнир"
        by_t[tname] = by_t.get(tname, 0) + 1

    parts = [f"Турниры сегодня ({ds.isoformat()}):"]
    for tn, n in by_t.items():
        parts.append(f"• {tn} — {n} матч(ей)")
    parts.append("\nДобавьте игроков: /watch Sinner")
    await tg_send_message(chat_id, "\n".join(parts))

async def _handle_watch(chat_id: int, payload: str):
    names = [x.strip() for x in (payload or "").split(",") if x.strip()]
    if not names:
        await tg_send_message(chat_id, "Формат: /watch ИмяИгрока (или несколько через запятую)")
        return

    added, asked = [], []
    today = _today(chat_id)

    for nm in names:
        en_full = _canon_en(nm)
        ru, known = ru_name_for(en_full)
        if known and ru:
            add_watch(chat_id, en_full, today)
            added.append(ru)
        else:
            suggestion = _auto_ru_guess(en_full)
            set_pending_alias(chat_id, en_full)
            asked.append(en_full)
            await tg_send_message(
                chat_id,
                f"Как записать *{en_full}* по-русски?\n\n"
                f"Вариант: _{suggestion}_\n"
                f"Или пришлите свой вариант одним сообщением.",
                parse_mode="Markdown"
            )

    parts = []
    if added:
        parts.append("Добавил:\n" + "\n".join(f"• {x}" for x in added))
    if asked:
        parts.append("\nЖду русскую запись для:\n" + "\n".join(f"• {x}" for x in asked))
    if parts:
        parts.append("\n/list — показать список на сегодня")
        await tg_send_message(chat_id, "\n".join(parts))

async def _handle_text_message(chat_id: int, text: str) -> bool:
    if text.startswith("/"):
        return False
    pending = consume_pending_alias(chat_id)
    if not pending:
        return False
    ru = (text or "").strip()
    if not ru:
        await tg_send_message(chat_id, "Пустой ответ. Пришлите, как записать имя по-русски.")
        return True
    set_alias(pending, ru)
    add_watch(chat_id, pending, _today(chat_id))
    await tg_send_message(chat_id, f"Сохранил: *{ru}* (EN: {pending}).\n/list — показать список", parse_mode="Markdown")
    return True

async def _read_body(receive) -> bytes:
    buf = b""
    while True:
        event = await receive()
        if event["type"] == "http.request":
            buf += event.get("body", b"")
            if not event.get("more_body", False):
                break
    return buf

async def app(scope, receive, send):
    assert scope["type"] == "http"
    method = scope.get("method", "GET").upper()

    # секрет из Telegram (если настроен)
    if WEBHOOK_SECRET and method == "POST":
        hdrs = dict((k.decode().lower(), v.decode()) for k, v in scope.get("headers", []))
        if hdrs.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            payload = json.dumps({"ok": False, "error": "forbidden"}, ensure_ascii=False).encode("utf-8")
            headers = [(b"content-type", b"application/json")]
            await send({"type": "http.response.start", "status": 403, "headers": headers})
            await send({"type": "http.response.body", "body": payload})
            return

    if method == "GET":
        body = json.dumps({"ok": True, "service": "webhook", "path": "/api/webhook"}, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json")]
        await send({"type":"http.response.start","status":200,"headers":headers})
        await send({"type":"http.response.body","body":body})
        return

    # POST — Telegram update
    try:
        ensure_schema()
    except Exception:
        pass

    raw = await _read_body(receive)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    # callback
    cb = payload.get("callback_query")
    if cb:
        chat_id = cb["message"]["chat"]["id"]
        data = cb.get("data") or ""
        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            await tg_answer_callback_query(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
        else:
            await tg_answer_callback_query(cb.get("id"))
        resp = {"ok": True, "action": "cb"}
    else:
        msg = payload.get("message") or payload.get("edited_message")
        if not msg:
            resp = {"ok": True, "ignored": True}
        else:
            chat_id = msg["chat"]["id"]
            text = (msg.get("text") or "").strip()

            # алиас-ответ
            handled = await _handle_text_message(chat_id, text)
            if not handled and text.startswith("/"):
                parts = text.split(maxsplit=1)
                cmd = parts[0].lstrip("/").lower()
                arg = parts[1] if len(parts) > 1 else ""

                if cmd in ("start", "menu"):
                    await _send_start(chat_id)
                elif cmd == "list":
                    await _send_watches_list(chat_id)
                elif cmd == "watch":
                    await _handle_watch(chat_id, arg)
                elif cmd == "settz":
                    tz = (arg or "").strip()
                    if not tz:
                        cur = get_tz(chat_id)
                        await tg_send_message(chat_id, f"Текущая TZ: {cur or '—'}. Пример: /settz Europe/Moscow")
                    else:
                        try:
                            ZoneInfo(tz)
                            set_tz(chat_id, tz)
                            await tg_send_message(chat_id, f"TZ обновлена: {tz}")
                        except Exception:
                            await tg_send_message(chat_id, "Некорректная TZ. Пример: Europe/Moscow")
                else:
                    await tg_send_message(chat_id, "Доступно: /start, /list, /watch, /settz")
                resp = {"ok": True}
            else:
                resp = {"ok": True}

    out = json.dumps(resp, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json")]
    await send({"type":"http.response.start","status":200,"headers":headers})
    await send({"type":"http.response.body","body":out})
