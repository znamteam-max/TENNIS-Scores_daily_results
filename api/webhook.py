# api/webhook.py
from __future__ import annotations

import os
from datetime import datetime, date
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Request
from zoneinfo import ZoneInfo

from db_pg import (
    ensure_schema,
    ensure_user,
    set_tz,
    get_tz,
    add_watch,
    clear_today,
    list_today,
    get_events_cache,
    set_events_cache,
)
from tg_api import send_message, answer_callback_query
from providers import sofascore as ss

# --- ВАЖНО: app объявлен на верхнем уровне модуля ---
app = FastAPI()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
_schema_ready = False

def _ensure_schema_safe() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        ensure_schema()
        _schema_ready = True
    except Exception:
        _schema_ready = False

def _today_local(chat_id: int) -> date:
    tzname = get_tz(chat_id)
    tz = ZoneInfo(tzname)
    return datetime.now(tz).date()

def _parse_names(text: str) -> List[str]:
    parts = [p.strip() for p in (text or "").split(",")]
    return [p for p in parts if p]

def _client() -> httpx.AsyncClient:
    common = dict(headers=ss.DEFAULT_HEADERS, follow_redirects=True, timeout=20.0)
    try:
        import h2  # noqa: F401
        return httpx.AsyncClient(http2=True, **common)
    except Exception:
        return httpx.AsyncClient(**common)

# Vercel мапит файл на /api/webhook, поэтому роуты внутри должны быть "/" (корень функции)
@app.get("")
@app.get("/")
def ping():
    _ensure_schema_safe()
    return {"ok": True, "service": "webhook"}

async def _send_tournaments_menu(chat_id: int) -> None:
    _ensure_schema_safe()
    today = _today_local(chat_id)

    events = get_events_cache(today)
    if not events:
        # пробуем прямую загрузку (может сработать), и сразу кладём в кэш
        try:
            async with _client() as client:
                events = await ss.events_by_date(client, today)
            if events:
                set_events_cache(today, events)
        except Exception:
            events = []

    if not events:
        await send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\n"
            "Кэш обычно пополняется в течение пары минут.\n\n"
            "Можно добавить игроков вручную: `/watch Rublev, Musetti`.",
        )
        return

    tours = ss.group_tournaments(events)
    if not tours:
        await send_message(chat_id, "Сегодня турниров нет или расписание недоступно.")
        return

    lines = ["Выберите турнир на сегодня:"]
    keyboard = []
    for i, t in enumerate(tours, 1):
        lines.append(f"{i}) {t['name']}")
        keyboard.append([{
            "text": f"{i}) {t['name']}",
            "callback_data": f"tour:{t['id']}",
        }])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})

async def _send_matches_menu(chat_id: int, tour_id: str) -> None:
    _ensure_schema_safe()
    today = _today_local(chat_id)

    events = get_events_cache(today)
    if not events:
        try:
            async with _client() as client:
                events = await ss.events_by_date(client, today)
            if events:
                set_events_cache(today, events)
        except Exception:
            events = []

    if not events:
        await send_message(chat_id, "Список матчей пока недоступен. Попробуйте /start позже.")
        return

    tours = ss.group_tournaments(events)
    tour = next((t for t in tours if t["id"] == tour_id), None)
    if not tour:
        await send_message(chat_id, "Турнир не найден или уже недоступен.")
        return

    lines = [f"Матчи: {tour['name']}"]
    keyboard = []
    for ev in tour["events"]:
        eid = ss.event_id_of(ev)
        hn = (ev.get("homeTeam") or {}).get("name", "—")
        an = (ev.get("awayTeam") or {}).get("name", "—")
        lines.append(f"• {hn} — {an}")
        keyboard.append([{
            "text": f"Следить: {hn} — {an}",
            "callback_data": f"watch_ev:{eid}",
        }])

    keyboard.append([{
        "text": "✅ Следить за ВСЕМИ матчами турнира",
        "callback_data": f"watch_tour:{tour_id}",
    }])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})

@app.post("")
@app.post("/")
async def webhook(req: Request):
    _ensure_schema_safe()

    if WEBHOOK_SECRET:
        token = req.headers.get("x-telegram-bot-api-secret-token")
        if token != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        upd = await req.json()
    except Exception:
        return {"ok": True}

    # --- CALLBACKS ---
    if "callback_query" in upd:
        cq = upd["callback_query"]
        cq_id = cq.get("id")
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
        data = (cq.get("data") or "").strip()
        if not chat_id:
            return {"ok": True}

        if data.startswith("tour:"):
            await answer_callback_query(cq_id)
            await _send_matches_menu(chat_id, data.split(":", 1)[1])
            return {"ok": True}

        if data.startswith("watch_ev:"):
            await answer_callback_query(cq_id, "Ок, добавил матч.")
            eid = data.split(":", 1)[1]
            try:
                today = _today_local(chat_id)
                events = get_events_cache(today)
                if not events:
                    async with _client() as client:
                        events = await ss.events_by_date(client, today)
                    if events:
                        set_events_cache(today, events)
            except Exception as e:
                await send_message(chat_id, f"Не удалось найти матч.\nTECH: {e}")
                return {"ok": True}

            existing = {(lbl or "").lower() for (lbl, _res, _src) in list_today(chat_id, today)}
            ev = next((e for e in events if ss.event_id_of(e) == eid), None)
            if ev:
                hn = (ev.get("homeTeam") or {}).get("name", "")
                an = (ev.get("awayTeam") or {}).get("name", "")
                if hn and hn.lower() not in existing:
                    add_watch(chat_id, hn, "sofascore", today); existing.add(hn.lower())
                if an and an.lower() not in existing:
                    add_watch(chat_id, an, "sofascore", today); existing.add(an.lower())
                await send_message(chat_id, f"Добавил на сегодня: {hn} и {an}. /list")
            else:
                await send_message(chat_id, "Матч уже недоступен.")
            return {"ok": True}

        if data.startswith("watch_tour:"):
            await answer_callback_query(cq_id, "Ок, добавил все матчи турнира.")
            tour_id = data.split(":", 1)[1]
            try:
                today = _today_local(chat_id)
                events = get_events_cache(today)
                if not events:
                    async with _client() as client:
                        events = await ss.events_by_date(client, today)
                    if events:
                        set_events_cache(today, events)
            except Exception as e:
                await send_message(chat_id, f"Не удалось получить турниры.\nTECH: {e}")
                return {"ok": True}

            tours = ss.group_tournaments(events)
            tour = next((t for t in tours if t["id"] == tour_id), None)
            if tour:
                today = _today_local(chat_id)
                existing = {(lbl or "").lower() for (lbl, _res, _src) in list_today(chat_id, today)}
                cnt = 0
                for ev in tour["events"]:
                    for nm in [
                        (ev.get("homeTeam") or {}).get("name", ""),
                        (ev.get("awayTeam") or {}).get("name", "")
                    ]:
                        if nm and nm.lower() not in existing:
                            add_watch(chat_id, nm, "sofascore", today)
                            existing.add(nm.lower()); cnt += 1
                await send_message(chat_id, f"Добавил {cnt} игроков из турнира. /list")
            else:
                await send_message(chat_id, "Турнир уже недоступен.")
            return {"ok": True}

        await answer_callback_query(cq_id)
        return {"ok": True}

    # --- MESSAGES ---
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    ensure_user(chat_id)
    text = (msg.get("text") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        await _send_tournaments_menu(chat_id); return {"ok": True}

    if text.startswith("/tz"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Укажите TZ, например: /tz Europe/Helsinki")
        else:
            import zoneinfo
            tz = toks[1].strip()
            try:
                _ = zoneinfo.ZoneInfo(tz); set_tz(chat_id, tz)
                await send_message(chat_id, f"Ок! Часовой пояс теперь {tz}.")
            except Exception:
                await send_message(chat_id, f"Неизвестная таймзона: {tz}")
        return {"ok": True}

    if text.startswith("/list"):
        rows = list_today(chat_id, _today_local(chat_id))
        if not rows:
            await send_message(chat_id, "На сегодня список пуст. Нажмите /start и выберите турнир.")
        else:
            today = _today_local(chat_id).isoformat()
            lines = [f"Сегодня ({today}):"]
            seen = set()
            for label, resolved, _src in rows:
                key = (label or "").lower()
                if key in seen: continue
                seen.add(key)
                lines.append(f"• {label}" + (f" (→ {resolved})" if resolved else ""))
            await send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    if text.startswith("/clear"):
        n = clear_today(chat_id, _today_local(chat_id))
        await send_message(chat_id, f"Ок, очистил список ({n} записей)."); return {"ok": True}

    if text.startswith("/watch"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /watch De Minaur, Musetti, Rublev"); return {"ok": True}
        names = _parse_names(toks[1])
        today = _today_local(chat_id)
        existing = {(lbl or "").lower() for (lbl, _res, _src) in list_today(chat_id, today)}
        for n in names:
            if n and n.lower() not in existing:
                add_watch(chat_id, n, "sofascore", today)
                existing.add(n.lower())
        await send_message(chat_id, "Добавил. /list"); return {"ok": True}

    await _send_tournaments_menu(chat_id)
    return {"ok": True}
