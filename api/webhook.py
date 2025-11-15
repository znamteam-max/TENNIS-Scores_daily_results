from __future__ import annotations

import os
import json
import datetime as dt
from typing import Any, Dict, List, Tuple, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from zoneinfo import ZoneInfo

import httpx

from db_pg import (
    ensure_schema,
    get_tz, set_tz,
    add_watch, add_watches, remove_watch, list_today,
    ru_name_for, set_alias,
    set_pending_alias, consume_pending_alias,
    get_events_cache,
)

# ------------- FastAPI app -------------

app = FastAPI(title="telegram-webhook")
handler = app  # для Vercel


# ------------- env -------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")

if not BOT_TOKEN:
    # не падаем — но отвечать в ТГ не сможем
    pass


# ------------- Telegram I/O -------------

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


# ------------- helpers -------------

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

def _format_list_with_ru(items: List[str]) -> str:
    lines = []
    for it in items:
        ru, known = ru_name_for(it)
        lines.append(f"• {ru if (ru and known) else it}")
    return "\n".join(lines) if lines else "—"


# ------------- UI blocks -------------

async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send_message(chat_id,
            "Сегодня (%s):\n—\n\nДобавьте игроков: /watch Rublev, Musetti" % day.isoformat()
        )
        return

    # инлайн-кнопки «удалить»
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send_message(
        chat_id,
        "Сегодня (%s):\n%s\n\nХотите исправить список?" % (day.isoformat(), _format_list_with_ru(arr)),
        reply_markup={"inline_keyboard": buttons}
    )


async def _send_start(chat_id: int):
    # показываем турниры/матчи из кэша (если есть), иначе — пояснение
    ds = _today(chat_id)
    data = get_events_cache(ds)
    if not data:
        await tg_send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\nКэш пополнится GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: /watch Rublev, Musetti."
        )
        return

    # ожидаем структуру вида {"events":[...]} — если иная, просто скажем, что расписание есть
    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []

    if not events:
        await tg_send_message(
            chat_id,
            "На сегодня список пуст. Нажмите /start позже или используйте /watch имя."
        )
        return

    # минимальное групирование по турнирам (без категорий 15/25/50 если есть)
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
        if not tname:
            tname = "Турнир"
        by_t.setdefault(tname, []).append(ev)

    parts = [f"Турниры сегодня ({ds.isoformat()}):"]
    for tn, evs in by_t.items():
        parts.append(f"\n• {tn} — {len(evs)} матч(ей)")

    parts.append("\nДобавьте игроков, чтобы бот подсвечивал их матчи: /watch Sinner")
    await tg_send_message(chat_id, "\n".join(parts))


# ------------- commands -------------

async def _handle_watch(chat_id: int, payload: str):
    """
    /watch <имя1, имя2 ...>
    - нормализуем EN (короткие формы → полное EN)
    - если нет RU-алиаса → спрашиваем «как записать» и сохраняем pending
    - если есть → сразу пишем в watches
    """
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
            # алиас известен → просто добавляем
            add_watch(chat_id, en_full, today)  # ВАЖНО: правильный порядок
            added.append(ru)
        else:
            # спрашиваем у юзера
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
    """
    Если ждём от чата RU-алиас — любое не-командное сообщение считаем ответом.
    """
    if text.startswith("/"):
        return False
    pending = consume_pending_alias(chat_id)
    if not pending:
        return False

    ru = text.strip()
    if not ru:
        await tg_send_message(chat_id, "Пустой ответ. Пришлите, как записать имя по-русски.")
        return True

    set_alias(pending, ru)
    add_watch(chat_id, pending, _today(chat_id))
    await tg_send_message(
        chat_id,
        f"Сохранил: *{ru}* (EN: {pending}).\n/list — показать список",
        parse_mode="Markdown"
    )
    return True


# ------------- routing -------------

@app.get("/")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "webhook", "path": "/api/webhook"})

@app.post("/")
async def webhook_abs(req: Request) -> JSONResponse:
    # секрет из заголовка Telegram
    if WEBHOOK_SECRET:
        if req.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="forbidden")

    ensure_schema()

    try:
        payload = await req.json()
    except Exception:
        payload = {}

    # callback_query (инлайн-кнопки)
    cb = payload.get("callback_query")
    if cb:
        chat_id = cb["message"]["chat"]["id"]
        data = cb.get("data") or ""
        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            await tg_answer_callback_query(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True, "action": "rm"})

        await tg_answer_callback_query(cb.get("id"))
        return JSONResponse({"ok": True, "action": "noop"})

    # обычное сообщение
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return JSONResponse({"ok": True, "ignored": True})

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    # если это ответ с алиасом (мы ждали) — обработаем и выходим
    if await _handle_text_message(chat_id, text):
        return JSONResponse({"ok": True})

    # команды
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("start", "menu"):
            await _send_start(chat_id)
            return JSONResponse({"ok": True})

        if cmd == "list":
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True})

        if cmd == "watch":
            await _handle_watch(chat_id, arg)
            return JSONResponse({"ok": True})

        if cmd == "settz":
            tz = (arg or "").strip()
            if not tz:
                await tg_send_message(chat_id, f"Текущая TZ: {get_tz(chat_id)}. Пример: /settz Europe/Moscow")
            else:
                try:
                    ZoneInfo(tz)
                    set_tz(chat_id, tz)
                    await tg_send_message(chat_id, f"TZ обновлена: {tz}")
                except Exception:
                    await tg_send_message(chat_id, "Некорректная TZ. Пример: Europe/Moscow")
            return JSONResponse({"ok": True})

        # неизвестная команда
        await tg_send_message(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")
        return JSONResponse({"ok": True})

    # ни команда, ни ожидаемый алиас — просто игнор
    return JSONResponse({"ok": True})
