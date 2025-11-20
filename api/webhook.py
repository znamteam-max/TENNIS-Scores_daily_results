from __future__ import annotations

import os, datetime as dt, json
from typing import Optional, List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# httpx fallback (если вдруг на Vercel его нет — используем stdlib)
try:
    import httpx
    _HAVE_HTTPX = True
except Exception:
    import urllib.request, urllib.error
    _HAVE_HTTPX = False

from db_pg import (
    ensure_schema,
    get_tz, set_tz,
    add_watch, remove_watch, list_today,
    ru_name_for, set_alias,
    set_pending_alias, consume_pending_alias,
    get_events_cache,
)

app = FastAPI(title="telegram-webhook")
handler = app

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/London")

# ---------- Telegram I/O ----------
def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

async def _http_post_json(url: str, payload: Dict[str, Any]) -> None:
    if _HAVE_HTTPX:
        async with httpx.AsyncClient(timeout=15.0) as c:
            await c.post(url, json=payload)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type":"application/json"})
        try:
            urllib.request.urlopen(req, timeout=15)
        except urllib.error.URLError:
            pass

async def tg_send_message(chat_id: int, text: str, **kwargs) -> None:
    if not BOT_TOKEN: return
    payload = {"chat_id": chat_id, "text": text}
    payload.update(kwargs)
    await _http_post_json(_tg_api("sendMessage"), payload)

async def tg_answer_callback_query(cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
    if not BOT_TOKEN: return
    payload = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text: payload["text"] = text
    await _http_post_json(_tg_api("answerCallbackQuery"), payload)

# ---------- helpers ----------
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
    out = []
    for it in items:
        pair = ru_name_for(it)
        if pair is None:
            out.append(f"• {it}")
            continue
        ru, known = pair
        out.append(f"• {ru if (ru and known) else it}")
    return "\n".join(out) if out else "—"

# ---- раскладка по категориям/турам из кэша ----
def _classify(ev: Dict[str, Any]) -> str:
    t = (ev or {}).get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = (ut.get("category") or t.get("category") or {})
    cname = (cat.get("name") or "").lower()
    uname = (ut.get("name") or t.get("name") or "").lower()
    if "challenger" in uname or "challenger" in cname:
        return "Challengers"
    if "atp" in cname and "challenger" not in cname:
        return "ATP"
    return "Другие"

def _tournament_name(ev: Dict[str, Any]) -> str:
    t = (ev or {}).get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return ut.get("name") or t.get("name") or "Турнир"

def _event_players(ev: Dict[str, Any]) -> Tuple[str, str]:
    home = ((ev or {}).get("homeTeam") or {}).get("name") or "Player A"
    away = ((ev or {}).get("awayTeam") or {}).get("name") or "Player B"
    return home, away

def _build_category_index(events: List[Dict[str, Any]]):
    by_cat: Dict[str, Dict[str, List[Dict[str, Any]]]] = {"ATP":{}, "Challengers":{}, "Другие":{}}
    for ev in events:
        cat = _classify(ev)
        tn = _tournament_name(ev)
        by_cat.setdefault(cat, {}).setdefault(tn, []).append(ev)
    return by_cat

# ---------- UI blocks ----------
async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send_message(chat_id,
            "Сегодня (%s):\n—\n\nДобавьте игроков: /watch Rublev, Musetti" % day.isoformat()
        )
        return
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send_message(
        chat_id,
        "Сегодня (%s):\n%s\n\nХотите исправить список?" % (day.isoformat(), _format_list_with_ru(arr)),
        reply_markup={"inline_keyboard": buttons}
    )

async def _send_start(chat_id: int):
    ds = _today(chat_id)
    data = get_events_cache(ds) or {}
    events = data.get("events") or data.get("list") or []
    if not events:
        await tg_send_message(
            chat_id,
            "Расписание на сегодня пока недоступно (источник ограничен).\n"
            "Кэш пополнится воркером.\n\n"
            "Можно добавить игроков вручную: /watch Sinner"
        )
        return
    by_cat = _build_category_index(events)
    counts = {k: sum(len(v) for v in by_cat.get(k, {}).values()) for k in ("ATP","Challengers","Другие")}
    text = (
        f"Турниры сегодня ({ds.isoformat()}):\n"
        f"ATP — {counts.get('ATP',0)} матч(ей)\n"
        f"Challengers — {counts.get('Challengers',0)} матч(ей)\n"
        f"Другие — {counts.get('Другие',0)} матч(ей)\n\n"
        "Выберите категорию:"
    )
    kb = [[{"text":"ATP","callback_data":"cat:ATP"}],
          [{"text":"Challengers","callback_data":"cat:Challengers"}],
          [{"text":"Другие","callback_data":"cat:Другие"}]]
    await tg_send_message(chat_id, text, reply_markup={"inline_keyboard": kb})

async def _send_tournaments(chat_id: int, category: str):
    ds = _today(chat_id)
    data = get_events_cache(ds) or {}
    events = data.get("events") or data.get("list") or []
    if not events:
        await tg_send_message(chat_id, "Пока пусто.")
        return
    by_cat = _build_category_index(events)
    tours = sorted((by_cat.get(category) or {}).keys())
    if not tours:
        await tg_send_message(chat_id, f"В категории {category} турниров нет.")
        return
    kb = [[{"text": name[:64], "callback_data": f"tour:{category}:{name[:64]}"}] for name in tours[:25]]
    await tg_send_message(chat_id, f"{category}: выберите турнир", reply_markup={"inline_keyboard": kb})

async def _send_players_for_tournament(chat_id: int, category: str, tour_name: str):
    ds = _today(chat_id)
    data = get_events_cache(ds) or {}
    events = data.get("events") or data.get("list") or []
    by_cat = _build_category_index(events)
    evs = (by_cat.get(category) or {}).get(tour_name) or []
    if not evs:
        await tg_send_message(chat_id, "Нет матчей.")
        return
    kb = []
    lines = [f"{tour_name} — матчи сегодня:"]
    for ev in evs[:40]:
        a, b = _event_players(ev)
        title = f"{a} vs {b}"
        lines.append(f"• {title}")
        # кнопка «подписаться» на обоих по отдельности
        kb.append([{"text": f"Следить: {a}", "callback_data": f"watch:{a}"},
                   {"text": f"Следить: {b}", "callback_data": f"watch:{b}"}])
        # если матч завершён — добавим кнопку «сформировать пост»
        st = (ev.get("status") or {}).get("type") or ""
        if str(st).lower() in ("finished","ended","after overtime"):
            kb.append([{"text": "Сформировать пост", "callback_data": f"mkpost:{a}::{b}"}])
    await tg_send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb})

# ---------- commands ----------
async def _handle_watch(chat_id: int, payload: str):
    names = [x.strip() for x in (payload or "").split(",") if x.strip()]
    if not names:
        await tg_send_message(chat_id, "Формат: /watch ИмяИгрока (или несколько через запятую)")
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
            await tg_send_message(
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

# ---------- routing ----------
@app.get("/")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "webhook", "path": "/api/webhook"})

@app.post("/")
async def webhook_abs(req: Request) -> JSONResponse:
    if WEBHOOK_SECRET:
        if req.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="forbidden")

    ensure_schema()

    try:
        payload = await req.json()
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
            await tg_answer_callback_query(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True, "action": "rm"})
        if data.startswith("cat:"):
            _, cat = data.split(":", 1)
            await tg_answer_callback_query(cb.get("id"))
            await _send_tournaments(chat_id, cat)
            return JSONResponse({"ok": True, "action": "cat"})
        if data.startswith("tour:"):
            _, cat, tour = data.split(":", 2)
            await tg_answer_callback_query(cb.get("id"))
            await _send_players_for_tournament(chat_id, cat, tour)
            return JSONResponse({"ok": True, "action": "tour"})
        if data.startswith("watch:"):
            _, nm = data.split(":", 1)
            await tg_answer_callback_query(cb.get("id"))
            await _handle_watch(chat_id, nm)
            return JSONResponse({"ok": True, "action": "watch"})
        if data.startswith("mkpost:"):
            await tg_answer_callback_query(cb.get("id"))
            await tg_send_message(chat_id, "Шаблон поста: *Матч завершён.* Счёт: 6-4 4-6 7-6(5).", parse_mode="Markdown")
            return JSONResponse({"ok": True, "action": "mkpost"})

        await tg_answer_callback_query(cb.get("id"))
        return JSONResponse({"ok": True, "action": "noop"})

    # обычное сообщение
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        return JSONResponse({"ok": True, "ignored": True})

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    if await _handle_text_message(chat_id, text):
        return JSONResponse({"ok": True})

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

        await tg_send_message(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})
