# api/webhook.py
from __future__ import annotations
import os, re
from datetime import datetime, date, timedelta
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Request
from zoneinfo import ZoneInfo

from db_pg import (
    ensure_schema, ensure_user, set_tz, get_tz,
    add_watch, clear_today, list_today, delete_watch,
    get_events_cache, set_events_cache, ru_name_for, norm_key, set_alias
)
from tg_api import send_message, answer_callback_query
from providers import sofascore as ss

app = FastAPI()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
_schema_ready = False

def _ensure_schema_safe() -> None:
    global _schema_ready
    if _schema_ready: return
    try:
        ensure_schema()
        _schema_ready = True
    except Exception:
        _schema_ready = False

def _tz(chat_id: int) -> ZoneInfo:
    return ZoneInfo(get_tz(chat_id))

def _today_local(chat_id: int) -> date:
    return datetime.now(_tz(chat_id)).date()

def _client() -> httpx.AsyncClient:
    common = dict(headers=ss.DEFAULT_HEADERS, follow_redirects=True, timeout=20.0)
    try:
        import h2  # noqa
        return httpx.AsyncClient(http2=True, **common)
    except Exception:
        return httpx.AsyncClient(**common)

def _fmt_start(ch_tz: ZoneInfo, dt_utc: datetime | None) -> str:
    if not dt_utc:
        return "время неизвестно"
    local = dt_utc.astimezone(ch_tz)
    delta = local - datetime.now(ch_tz)
    mins = int(delta.total_seconds() // 60)
    if mins >= 0:
        h, m = divmod(mins, 60)
        left = (f"{h}ч {m}м" if h else f"{m}м")
        return f"{local:%H:%M}, через {left}"
    else:
        mins = abs(mins)
        h, m = divmod(mins, 60)
        ago = (f"{h}ч {m}м" if h else f"{m}м")
        return f"{local:%H:%M}, {ago} назад"

def _find_event_for_name(events: List[Dict[str, Any]], name_ru_or_en: str) -> Dict[str, Any] | None:
    # ищем по частичному совпадению в именах
    key = norm_key(name_ru_or_en)
    for ev in events:
        hn = (ev.get("homeTeam") or {}).get("name", "")
        an = (ev.get("awayTeam") or {}).get("name", "")
        if key and (key in norm_key(hn) or key in norm_key(an)):
            return ev
    return None

# --------- пинги (оба пути) ----------
@app.get("")
@app.get("/")
def ping_root():
    _ensure_schema_safe()
    return {"ok": True, "service": "webhook", "path": "/"}

@app.get("/api/webhook")
def ping_abs():
    _ensure_schema_safe()
    return {"ok": True, "service": "webhook", "path": "/api/webhook"}

# --------- бизнес-логика ----------
async def _load_events_for_today(chat_id: int) -> List[Dict[str, Any]]:
    today = _today_local(chat_id)
    events = get_events_cache(today)
    if events:
        return events
    # пробуем подтянуть из сети и закешировать
    try:
        async with _client() as client:
            events = await ss.events_by_date(client, today)
        if events:
            set_events_cache(today, events)
    except Exception:
        events = []
    return events

async def _send_tournaments_menu(chat_id: int) -> None:
    _ensure_schema_safe()
    events = await _load_events_for_today(chat_id)
    if not events:
        await send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\n"
            "Кэш пополнится GitHub-воркером.\n\n"
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
    events = await _load_events_for_today(chat_id)
    tours = ss.group_tournaments(events)
    tour = next((t for t in tours if t["id"] == tour_id), None)
    if not tour:
        await send_message(chat_id, "Турнир не найден или уже недоступен.")
        return
    tz = _tz(chat_id)
    lines = [f"Матчи: {tour['name']}"]
    keyboard = []
    for ev in tour["events"]:
        meta = ss.event_status(ev)
        hn, an = meta["home"], meta["away"]
        when = _fmt_start(tz, meta["start"])
        lines.append(f"• {hn} — {an}  ({when})")
        keyboard.append([{
            "text": f"Следить: {hn} — {an}",
            "callback_data": f"watch_ev:{ss.event_id_of(ev)}",
        }])
    keyboard.append([{
        "text": "✅ Следить за ВСЕМИ матчами турнира",
        "callback_data": f"watch_tour:{tour_id}",
    }])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})

async def _send_list(chat_id: int) -> None:
    today = _today_local(chat_id)
    rows = list_today(chat_id, today)
    if not rows:
        await send_message(chat_id, "На сегодня список пуст. Нажмите /start и выберите турнир.")
        return
    tz = _tz(chat_id)
    events = await _load_events_for_today(chat_id)
    lines = [f"Сегодня ({today.isoformat()}):"]
    kb_rows = []
    for idx, (label, _resolved, _src) in enumerate(rows, 1):
        ev = _find_event_for_name(events, label) if events else None
        if ev:
            st = ss.event_status(ev)
            when = _fmt_start(tz, st["start"])
            lines.append(f"• {label}  ({when})")
        else:
            lines.append(f"• {label}")
        kb_rows.append([{"text": f"❌ Удалить: {label}", "callback_data": f"del:{label}"}])
    kb_rows.append([{"text": "🧹 Очистить список", "callback_data": "clear:today"}])
    await send_message(
        chat_id,
        "\n".join(lines) + "\n\nХотите исправить список?",
        reply_markup={"inline_keyboard": kb_rows}
    )

# --------- основной обработчик ----------
async def _handle(req: Request):
    _ensure_schema_safe()
    if WEBHOOK_SECRET:
        token = req.headers.get("x-telegram-bot-api-secret-token")
        if token != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")
    try:
        upd = await req.json()
    except Exception:
        return {"ok": True}

    # callback-кнопки
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
            events = await _load_events_for_today(chat_id)
            ev = next((e for e in events if ss.event_id_of(e) == eid), None)
            if ev:
                hn = (ev.get("homeTeam") or {}).get("name", "")
                an = (ev.get("awayTeam") or {}).get("name", "")
                for nm in (hn, an):
                    rn = ru_name_for(nm)
                    add_watch(chat_id, rn, "sofascore", _today_local(chat_id))
                await send_message(chat_id, f"Добавил на сегодня: {hn} и {an}. /list")
            else:
                await send_message(chat_id, "Матч уже недоступен.")
            return {"ok": True}

        if data.startswith("watch_tour:"):
            await answer_callback_query(cq_id, "Ок, добавил все матчи турнира.")
            tour_id = data.split(":", 1)[1]
            events = await _load_events_for_today(chat_id)
            tours = ss.group_tournaments(events)
            tour = next((t for t in tours if t["id"] == tour_id), None)
            if not tour:
                await send_message(chat_id, "Турнир уже недоступен.")
                return {"ok": True}
            cnt = 0
            for ev in tour["events"]:
                hn = (ev.get("homeTeam") or {}).get("name", "")
                an = (ev.get("awayTeam") or {}).get("name", "")
                for nm in (hn, an):
                    rn = ru_name_for(nm)
                    add_watch(chat_id, rn, "sofascore", _today_local(chat_id))
                    cnt += 1
            await send_message(chat_id, f"Добавил {cnt} игроков из турнира. /list")
            return {"ok": True}

        if data.startswith("del:"):
            await answer_callback_query(cq_id, "Удалено")
            label = data.split(":", 1)[1]
            n = delete_watch(chat_id, label, _today_local(chat_id))
            if n == 0:
                await send_message(chat_id, "Не нашёл такую запись, возможно уже удалена.")
            else:
                await _send_list(chat_id)
            return {"ok": True}

        if data == "clear:today":
            await answer_callback_query(cq_id, "Список очищен")
            clear_today(chat_id, _today_local(chat_id))
            await send_message(chat_id, "Ок, пусто. Нажмите /start, чтобы выбрать заново.")
            return {"ok": True}

        await answer_callback_query(cq_id)
        return {"ok": True}

    # сообщения
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return {"ok": True}

    ensure_user(chat_id)
    text = (msg.get("text") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        await _send_tournaments_menu(chat_id); return {"ok": True}

    if text.startswith("/list"):
        await _send_list(chat_id); return {"ok": True}

    if text.startswith("/clear"):
        clear_today(chat_id, _today_local(chat_id))
        await send_message(chat_id, "Ок, очистил список."); return {"ok": True}

    if text.startswith("/tz"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Укажите TZ, например: /tz Europe/Helsinki")
        else:
            import zoneinfo
            try:
                tz = toks[1].strip()
                _ = zoneinfo.ZoneInfo(tz)
                set_tz(chat_id, tz)
                await send_message(chat_id, f"Ок! Часовой пояс теперь {tz}.")
            except Exception:
                await send_message(chat_id, "Неизвестная таймзона.")
        return {"ok": True}

    if text.startswith("/setru"):
        # /setru Jannik Sinner = Янник Синнер
        m = re.match(r"^/setru\s+(.+?)\s*[:=]\s*(.+)$", text)
        if not m:
            await send_message(chat_id, "Формат: /setru Jannik Sinner = Янник Синнер")
            return {"ok": True}
        latin, ru = m.group(1).strip(), m.group(2).strip()
        set_alias(latin, ru)
        await send_message(chat_id, f"Ок, запомнил: {latin} → {ru}")
        return {"ok": True}

    if text.startswith("/watch"):
        # /watch Rublev, Musetti  | можно писать по-русски
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /watch De Minaur, Musetti")
            return {"ok": True}
        raw = [p.strip() for p in toks[1].split(",") if p.strip()]
        for nm in raw:
            rn = ru_name_for(nm)
            add_watch(chat_id, rn, "sofascore", _today_local(chat_id))
        await _send_list(chat_id)
        return {"ok": True}

    # по умолчанию — меню турниров
    await _send_tournaments_menu(chat_id)
    return {"ok": True}

# маппинг путей, чтобы не ловить 404 от root_path
@app.post("")
@app.post("/")
async def webhook_root(req: Request):
    return await _handle(req)

@app.post("/api/webhook")
async def webhook_abs(req: Request):
    return await _handle(req)
