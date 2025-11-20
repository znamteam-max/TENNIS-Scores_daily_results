# api/webhook.py — чистый ASGI, минимальный Telegram webhook
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

def _canon_en(token: str) -> str:
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
    t = token.strip()
    k = " ".join(t.lower().split())
    return KNOWN_EN.get(k, t)

def _tz_for(chat_id: int) -> ZoneInfo:
    try:
        return ZoneInfo(get_tz(chat_id) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)

def _today(chat_id: int) -> dt.date:
    return dt.datetime.now(_tz_for(chat_id)).date()

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

async def tg_send(chat_id: int, text: str, **kwargs):
    if not BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text}
    payload.update(kwargs)
    async with httpx.AsyncClient(timeout=15.0) as c:
        await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

async def tg_answer(cb_id: str, text: Optional[str] = None, show_alert: bool = False):
    if not BOT_TOKEN:
        return
    payload = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    async with httpx.AsyncClient(timeout=15.0) as c:
        await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json=payload)

def _format_list_with_ru(items: List[str]) -> str:
    lines = []
    for it in items:
        pair = ru_name_for(it)
        if pair and pair[0] and pair[1]:
            lines.append(f"• {pair[0]}")
        else:
            lines.append(f"• {it}")
    return "\n".join(lines) if lines else "—"

async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send(chat_id, f"Сегодня ({day.isoformat()}):\n—\n\nДобавьте игроков: /watch Rublev, Musetti")
        return
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send(
        chat_id,
        f"Сегодня ({day.isoformat()}):\n{_format_list_with_ru(arr)}\n\nХотите исправить список?",
        reply_markup={"inline_keyboard": buttons}
    )

async def _send_start(chat_id: int):
    ds = _today(chat_id)
    data = get_events_cache(ds)
    if not data:
        await tg_send(chat_id,
            "Расписание сегодня пока недоступно.\nКэш пополнится GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: /watch Rublev, Musetti."
        )
        return

    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []

    if not events:
        await tg_send(chat_id, "На сегодня список пуст. Нажмите /start позже или используйте /watch имя.")
        return

    # Грубое группирование по турнирам + фильтр 15/25/50
    by_t = {}
    for ev in events:
        cat_id = (
            (((ev.get("tournament") or {}).get("category") or {}).get("id"))
            if isinstance(ev, dict) else None
        )
        if cat_id in (15, 25, 50):
            continue
        tname = None
        if isinstance(ev, dict):
            t = ev.get("tournament") or {}
            ut = t.get("uniqueTournament") or {}
            tname = ut.get("name") or t.get("name")
        by_t.setdefault(tname or "Турнир", []).append(ev)

    parts = [f"Турниры сегодня ({ds.isoformat()}):"]
    for tn, evs in by_t.items():
        parts.append(f"\n• {tn} — {len(evs)} матч(ей)")
    parts.append("\nДобавьте игроков, чтобы бот подсвечивал их матчи: /watch Sinner")
    await tg_send(chat_id, "\n".join(parts))

async def _handle_watch(chat_id: int, payload: str):
    names = [x.strip() for x in (payload or "").split(",") if x.strip()]
    if not names:
        await tg_send(chat_id, "Формат: /watch ИмяИгрока (или несколько через запятую)")
        return
    added, asked = [], []
    today = _today(chat_id)

    for nm in names:
        en_full = _canon_en(nm)
        pair = ru_name_for(en_full)
        if pair is None:
            suggestion = _auto_ru_guess(en_full)
            set_pending_alias(chat_id, en_full)
            asked.append(en_full)
            await tg_send(
                chat_id,
                f"Как записать *{en_full}* по-русски?\n\n"
                f"Вариант: _{suggestion}_\n"
                f"Или пришлите свой вариант одним сообщением.",
                parse_mode="Markdown"
            )
            continue

        ru, known = pair
        if known and ru:
            add_watch(chat_id, en_full, today)
            added.append(ru)
        else:
            suggestion = _auto_ru_guess(en_full)
            set_pending_alias(chat_id, en_full)
            asked.append(en_full)
            await tg_send(
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
        await tg_send(chat_id, "\n".join(parts))

async def _handle_text_alias(chat_id: int, text: str) -> bool:
    if text.startswith("/"):
        return False
    pending = consume_pending_alias(chat_id)
    if not pending:
        return False
    ru = (text or "").strip()
    if not ru:
        await tg_send(chat_id, "Пустой ответ. Пришлите, как записать имя по-русски.")
        return True
    set_alias(pending, ru)
    add_watch(chat_id, pending, _today(chat_id))
    await tg_send(chat_id, f"Сохранил: *{ru}* (EN: {pending}).\n/list — показать список", parse_mode="Markdown")
    return True

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return
    # секрет
    headers = dict((k.decode().lower(), v.decode()) for k, v in scope.get("headers", []))
    if WEBHOOK_SECRET:
        if headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            await _json(send, {"error": "forbidden"}, 403)
            return

    if scope.get("method") == "GET":
        await _json(send, {"ok": True, "service": "webhook", "path": "/api/webhook"}, 200)
        return

    if scope.get("method") != "POST":
        await _json(send, {"error": "method not allowed"}, 405)
        return

    ensure_schema()

    # читаем тело
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

    try:
        payload = json.loads(body.decode() or "{}")
    except Exception:
        payload = {}

    # callback_query
    cb = payload.get("callback_query")
    if cb:
        chat_id = cb["message"]["chat"]["id"]
        data = cb.get("data") or ""
        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            await tg_answer(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
            await _json(send, {"ok": True, "action": "rm"}, 200)
            return
        await tg_answer(cb.get("id"))
        await _json(send, {"ok": True, "action": "noop"}, 200)
        return

    # message
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        await _json(send, {"ok": True, "ignored": True}, 200)
        return

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    if await _handle_text_alias(chat_id, text):
        await _json(send, {"ok": True}, 200)
        return

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("start", "menu"):
            await _send_start(chat_id)
            await _json(send, {"ok": True}, 200); return
        if cmd == "list":
            await _send_watches_list(chat_id)
            await _json(send, {"ok": True}, 200); return
        if cmd == "watch":
            await _handle_watch(chat_id, arg)
            await _json(send, {"ok": True}, 200); return
        if cmd == "settz":
            tz = (arg or "").strip()
            if not tz:
                await tg_send(chat_id, f"Текущая TZ: {get_tz(chat_id)}. Пример: /settz Europe/Moscow")
            else:
                try:
                    ZoneInfo(tz)
                    set_tz(chat_id, tz)
                    await tg_send(chat_id, f"TZ обновлена: {tz}")
                except Exception:
                    await tg_send(chat_id, "Некорректная TZ. Пример: Europe/Moscow")
            await _json(send, {"ok": True}, 200); return

        await tg_send(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")
        await _json(send, {"ok": True}, 200); return

    await _json(send, {"ok": True}, 200)

async def _json(send, obj: Dict[str, Any], status: int = 200):
    body = json.dumps(obj).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
