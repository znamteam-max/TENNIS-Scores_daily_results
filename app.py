import os, httpx
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import List
from fastapi import FastAPI, Request, HTTPException

from db_pg import (
    ensure_schema, ensure_user, set_tz, get_tz,
    add_watch, clear_today, list_today
)
from tg_api import send_message, answer_callback_query
from formatter import build_match_message
from providers import sofascore as ss

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

app = FastAPI()

# --- ленивая инициализация схемы БД ---
_schema_ready = False
def _ensure_schema_safe():
    global _schema_ready
    if _schema_ready:
        return
    try:
        ensure_schema()
        _schema_ready = True
    except Exception:
        _schema_ready = False

# --------- helpers ----------
def _today_local(chat_id: int) -> date:
    tz = ZoneInfo(get_tz(chat_id))
    return datetime.now(tz).date()

def _parse_names(text: str) -> List[str]:
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]

# --------- UI screens ----------
async def _send_tournaments_menu(chat_id: int):
    _ensure_schema_safe()
    async with httpx.AsyncClient() as client:
        events = await ss.events_by_date(client, _today_local(chat_id))
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
            "callback_data": f"tour:{t['id']}"
        }])

    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})

async def _send_matches_menu(chat_id: int, tour_id: str):
    _ensure_schema_safe()
    async with httpx.AsyncClient() as client:
        events = await ss.events_by_date(client, _today_local(chat_id))
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
            "callback_data": f"watch_ev:{eid}"
        }])

    keyboard.append([{
        "text": "✅ Следить за ВСЕМИ матчами турнира",
        "callback_data": f"watch_tour:{tour_id}"
    }])

    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})

# --------- health/hello ----------
@app.get("/api/hello")
def hello():
    _ensure_schema_safe()
    return {"ok": True}

# --------- webhook (GET ping + POST апдейты) ----------
@app.get("/api/webhook")
@app.get("/api/webhook/")
def webhook_ping():
    _ensure_schema_safe()
    return {"ok": True, "service": "webhook"}

@app.post("/api/webhook")
@app.post("/api/webhook/")
async def webhook(req: Request):
    _ensure_schema_safe()

    # защитный заголовок от Телеграма
    if WEBHOOK_SECRET:
        token = req.headers.get("x-telegram-bot-api-secret-token")
        if token != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")

    # читаем апдейт
    try:
        upd = await req.json()
    except Exception:
        return {"ok": True}

    # --- callback_query ---
    if "callback_query" in upd:
        cq = upd["callback_query"]
        cq_id = cq.get("id")
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
        data = (cq.get("data") or "")
        if not chat_id:
            return {"ok": True}

        if data.startswith("tour:"):
            await answer_callback_query(cq_id)
            await _send_matches_menu(chat_id, data.split(":", 1)[1])
            return {"ok": True}

        if data.startswith("watch_ev:"):
            await answer_callback_query(cq_id, "Ок, добавил матч.")
            eid = data.split(":", 1)[1]
            async with httpx.AsyncClient() as client:
                events = await ss.events_by_date(client, _today_local(chat_id))
            ev = next((e for e in events if ss.event_id_of(e) == eid), None)
            if ev:
                today = _today_local(chat_id)
                hn = (ev.get("homeTeam") or {}).get("name", "")
                an = (ev.get("awayTeam") or {}).get("name", "")
                if hn: add_watch(chat_id, hn, "sofascore", today)
                if an: add_watch(chat_id, an, "sofascore", today)
                await send_message(chat_id, f"Добавил на сегодня: {hn} и {an}. /list")
            else:
                await send_message(chat_id, "Матч уже недоступен.")
            return {"ok": True}

        if data.startswith("watch_tour:"):
            await answer_callback_query(cq_id, "Ок, добавил все матчи турнира.")
            tour_id = data.split(":", 1)[1]
            async with httpx.AsyncClient() as client:
                events = await ss.events_by_date(client, _today_local(chat_id))
            tours = ss.group_tournaments(events)
            tour = next((t for t in tours if t["id"] == tour_id), None)
            if tour:
                today = _today_local(chat_id)
                cnt = 0
                seen = set()
                for ev in tour["events"]:
                    for nm in [(ev.get("homeTeam") or {}).get("name",""),
                               (ev.get("awayTeam") or {}).get("name","")]:
                        if nm and nm not in seen:
                            add_watch(chat_id, nm, "sofascore", today)
                            seen.add(nm)
                            cnt += 1
                await send_message(chat_id, f"Добавил {cnt} игроков из турнира. /list")
            else:
                await send_message(chat_id, "Турнир уже недоступен.")
            return {"ok": True}

        await answer_callback_query(cq_id)
        return {"ok": True}

    # --- обычные сообщения ---
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}

    ensure_user(chat_id)
    text = (msg.get("text") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        await _send_tournaments_menu(chat_id)
        return {"ok": True}

    if text.startswith("/tz"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Укажите TZ, например: /tz Europe/Helsinki")
        else:
            import zoneinfo
            tz = toks[1].strip()
            try:
                _ = zoneinfo.ZoneInfo(tz)
                set_tz(chat_id, tz)
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
            for label, resolved, _ in rows:
                lines.append(f"• {label}" + (f" (→ {resolved})" if resolved else ""))
            await send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    if text.startswith("/clear"):
        n = clear_today(chat_id, _today_local(chat_id))
        await send_message(chat_id, f"Ок, очистил список ({n} записей).")
        return {"ok": True}

    if text.startswith("/watch"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /watch De Minaur, Musetti, Rublev")
            return {"ok": True}
        names = _parse_names(toks[1])
        today = _today_local(chat_id)
        for n in names:
            add_watch(chat_id, n, "sofascore", today)
        await send_message(chat_id, "Добавил. /list")
        return {"ok": True}

    return {"ok": True}
