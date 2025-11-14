# api/webhook.py
from __future__ import annotations
import os, re
from datetime import datetime, date
from typing import List, Dict, Any
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Request, HTTPException

from db_pg import (
    ensure_schema, ensure_user, get_tz, set_tz,
    ru_name_for, set_alias,
    add_watch, delete_watch, clear_today, list_today,
    get_events_cache, set_events_cache, norm_key
)
from providers import sofascore as ss
from tg_api import send_message, answer_callback_query

app = FastAPI()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
_schema_ok = False

def _ensure_schema():
    global _schema_ok
    if _schema_ok: return
    try:
        ensure_schema()
        _schema_ok = True
    except Exception:
        _schema_ok = False

def _tz(chat_id: int) -> ZoneInfo:
    return ZoneInfo(get_tz(chat_id))

def _today(chat_id: int) -> date:
    return datetime.now(_tz(chat_id)).date()

def _client() -> httpx.AsyncClient:
    common = dict(headers=ss.DEFAULT_HEADERS, follow_redirects=True, timeout=25.0)
    try:
        import h2  # noqa
        return httpx.AsyncClient(http2=True, **common)
    except Exception:
        return httpx.AsyncClient(**common)

def _fmt_start(ch_tz: ZoneInfo, dt_utc: datetime | None) -> str:
    if not dt_utc:
        return "время неизвестно"
    local = dt_utc.astimezone(ch_tz)
    mins = int((local - datetime.now(ch_tz)).total_seconds() // 60)
    if mins >= 0:
        h, m = divmod(mins, 60)
        return f"{local:%H:%M}, через {h}ч {m}м" if h else f"{local:%H:%M}, через {m}м"
    mins = -mins
    h, m = divmod(mins, 60)
    return f"{local:%H:%M}, {h}ч {m}м назад" if h else f"{local:%H:%M}, {m}м назад"

def _event_for_label(events: List[Dict[str, Any]], label_ru: str) -> Dict[str, Any] | None:
    key = norm_key(label_ru)
    for ev in events:
        hn = (ev.get("homeTeam") or {}).get("name","")
        an = (ev.get("awayTeam") or {}).get("name","")
        if key in norm_key(hn) or key in norm_key(an):
            return ev
    return None

async def _load_events(chat_id: int) -> List[Dict[str, Any]]:
    d = _today(chat_id)
    cached = get_events_cache(d)
    if cached: return cached
    try:
        async with _client() as c:
            events = await ss.events_by_date(c, d)
        if events:
            set_events_cache(d, events)
        return events
    except Exception:
        return []

# ---------- пинги (оба пути) ----------
@app.get("/")
def ping_root():
    _ensure_schema()
    return {"ok": True, "service": "webhook", "path": "/api/webhook"}

@app.get("/api/webhook")
def ping_abs():
    _ensure_schema()
    return {"ok": True, "service": "webhook", "path": "/api/webhook"}

# ---------- основной обработчик ----------
async def _handle(req: Request):
    _ensure_schema()
    if WEBHOOK_SECRET:
        if req.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        upd = await req.json()
    except Exception:
        return {"ok": True}

    # ----- callbacks -----
    if "callback_query" in upd:
        cq = upd["callback_query"]
        cq_id = cq.get("id")
        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
        data = (cq.get("data") or "")

        if not chat_id:
            return {"ok": True}

        # выбор турнира
        if data.startswith("tour:"):
            await answer_callback_query(cq_id)
            tour_id = data.split(":",1)[1]
            events = await _load_events(chat_id)
            tours = ss.group_tournaments(events)
            tour = next((t for t in tours if t["id"] == tour_id), None)
            if not tour:
                await send_message(chat_id, "Турнир не найден.")
                return {"ok": True}
            tz = _tz(chat_id)
            lines, kb = [f"Матчи: {tour['name']}"], []
            for ev in tour["events"]:
                meta = ss.event_status(ev)
                when = _fmt_start(tz, meta["start"])
                hn, an = meta["home"], meta["away"]
                lines.append(f"• {hn} — {an}  ({when})")
                kb.append([{"text": f"Следить: {hn} — {an}",
                           "callback_data": f"watch_ev:{ss.event_id_of(ev)}"}])
            kb.append([{"text": "✅ Следить за ВСЕМИ матчами турнира",
                        "callback_data": f"watch_tour:{tour_id}"}])
            await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb})
            return {"ok": True}

        # добавить обеих игроков матча
        if data.startswith("watch_ev:"):
            await answer_callback_query(cq_id, "Добавляю…")
            eid = data.split(":",1)[1]
            events = await _load_events(chat_id)
            ev = next((e for e in events if ss.event_id_of(e) == eid), None)
            if ev:
                for nm in [(ev.get("homeTeam") or {}).get("name",""),
                           (ev.get("awayTeam") or {}).get("name","")]:
                    ru, known = ru_name_for(nm)
                    if known:
                        add_watch(chat_id, ru, _today(chat_id))
                    else:
                        # спросить, как записать
                        guess = _simple_translit(nm)
                        await send_message(
                            chat_id,
                            f"Как записать по-русски: «{nm}»?",
                            reply_markup={"inline_keyboard":[
                                [{"text": f"✅ {guess}", "callback_data": f"alias:set:{nm}|{guess}"}],
                                [{"text": "Оставить как есть", "callback_data": f"alias:set:{nm}|{nm}"}],
                            ]}
                        )
                await send_message(chat_id, "Готово. /list")
            else:
                await send_message(chat_id, "Матч уже недоступен.")
            return {"ok": True}

        # добавить всех из турнира
        if data.startswith("watch_tour:"):
            await answer_callback_query(cq_id, "Добавляю всех…")
            tour_id = data.split(":",1)[1]
            events = await _load_events(chat_id)
            tour = next((t for t in ss.group_tournaments(events) if t["id"] == tour_id), None)
            if not tour:
                await send_message(chat_id, "Турнир уже недоступен.")
                return {"ok": True}
            for ev in tour["events"]:
                for nm in [(ev.get("homeTeam") or {}).get("name",""),
                           (ev.get("awayTeam") or {}).get("name","")]:
                    ru, known = ru_name_for(nm)
                    if known:
                        add_watch(chat_id, ru, _today(chat_id))
                    else:
                        guess = _simple_translit(nm)
                        await send_message(
                            chat_id,
                            f"Как записать по-русски: «{nm}»?",
                            reply_markup={"inline_keyboard":[
                                [{"text": f"✅ {guess}", "callback_data": f"alias:set:{nm}|{guess}"}],
                                [{"text": "Оставить как есть", "callback_data": f"alias:set:{nm}|{nm}"}],
                            ]}
                        )
            await send_message(chat_id, "Готово. /list")
            return {"ok": True}

        # подтверждение алиаса
        if data.startswith("alias:set:"):
            await answer_callback_query(cq_id, "Запомнил")
            tail = data.split(":",2)[2]
            latin, ru = tail.split("|",1)
            set_alias(latin, ru)
            add_watch(chat_id, ru, _today(chat_id))
            await send_message(chat_id, f"Сохранил: {latin} → {ru}\n/list")
            return {"ok": True}

        # удалить/очистить
        if data.startswith("del:"):
            await answer_callback_query(cq_id, "Удалено")
            lbl = data.split(":",1)[1]
            delete_watch(chat_id, lbl, _today(chat_id))
            await _send_list(chat_id)
            return {"ok": True}
        if data == "clear:today":
            await answer_callback_query(cq_id, "Очищено")
            clear_today(chat_id, _today(chat_id))
            await send_message(chat_id, "Список очищен. Нажмите /start.")
            return {"ok": True}

        # сформировать пост по доигранному матчу
        if data.startswith("post:"):
            await answer_callback_query(cq_id, "Формирую пост…")
            eid = data.split(":",1)[1]
            try:
                async with _client() as c:
                    stats = await ss.event_stats_any(c, eid)
                text = _render_post_from_stats(stats)
                await send_message(chat_id, text, parse_mode="HTML")
            except Exception as e:
                await send_message(chat_id, f"Не удалось собрать статистику.\nTECH: {e}")
            return {"ok": True}

        await answer_callback_query(cq_id)
        return {"ok": True}

    # ----- обычные сообщения -----
    msg = upd.get("message") or upd.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id:
        return {"ok": True}

    ensure_user(chat_id)
    text = (msg.get("text") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        await _send_tournaments_menu(chat_id); return {"ok": True}

    if text.startswith("/tz"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            try:
                ZoneInfo(parts[1])
                set_tz(chat_id, parts[1]); await send_message(chat_id, "OK!")
            except Exception:
                await send_message(chat_id, "Неизвестный TZ. Пример: /tz Europe/Helsinki")
        else:
            await send_message(chat_id, f"Ваш TZ: {get_tz(chat_id)}")
        return {"ok": True}

    if text.startswith("/list"):
        await _send_list(chat_id); return {"ok": True}

    if text.startswith("/clear"):
        clear_today(chat_id, _today(chat_id)); await send_message(chat_id, "Очищено."); return {"ok": True}

    if text.startswith("/watch"):
        body = text.split(maxsplit=1)[1] if " " in text else ""
        if not body:
            await send_message(chat_id, "Пример: /watch De Minaur, Musetti"); return {"ok": True}
        names = [p.strip() for p in body.split(",") if p.strip()]
        for nm in names:
            ru, known = ru_name_for(nm)
            if known:
                add_watch(chat_id, ru, _today(chat_id))
            else:
                guess = _simple_translit(nm)
                await send_message(
                    chat_id,
                    f"Как записать по-русски: «{nm}»?",
                    reply_markup={"inline_keyboard":[
                        [{"text": f"✅ {guess}", "callback_data": f"alias:set:{nm}|{guess}"}],
                        [{"text": "Оставить как есть", "callback_data": f"alias:set:{nm}|{nm}"}],
                    ]}
                )
        await _send_list(chat_id)
        return {"ok": True}

    # по умолчанию
    await _send_tournaments_menu(chat_id)
    return {"ok": True}

# --- вспомогательные экраны ---
async def _send_tournaments_menu(chat_id: int):
    events = await _load_events(chat_id)
    if not events:
        await send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\n"
            "Кэш пополнится GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: /watch Rublev, Musetti."
        ); return
    tours = ss.group_tournaments(events)
    if not tours:
        await send_message(chat_id, "Сегодня турниров нет."); return
    kb, lines = [], ["Выберите турнир на сегодня:"]
    for i, t in enumerate(tours, 1):
        lines.append(f"{i}) {t['name']}")
        kb.append([{"text": f"{i}) {t['name']}", "callback_data": f"tour:{t['id']}"}])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb})

async def _send_list(chat_id: int):
    today = _today(chat_id)
    rows = list_today(chat_id, today)
    if not rows:
        await send_message(chat_id, "На сегодня список пуст. Нажмите /start."); return
    tz = _tz(chat_id)
    events = await _load_events(chat_id)
    lines, kb = [f"Сегодня ({today.isoformat()}):"], []
    finished_buttons = []
    for label, _resolved, _src in rows:
        ev = _event_for_label(events, label) if events else None
        if ev:
            meta = ss.event_status(ev)
            when = _fmt_start(tz, meta["start"])
            state = (meta["state"] or "").upper()
            lines.append(f"• {label}  ({when})")
            # если матч доигран — предложим сформировать пост
            if state in ("FINISHED", "ENDED", "AFTER_EXTRA_TIME"):
                finished_buttons.append([{"text": f"📝 Пост: {meta['home']} — {meta['away']}",
                                         "callback_data": f"post:{ss.event_id_of(ev)}"}])
        else:
            lines.append(f"• {label}")
        kb.append([{"text": f"❌ Удалить: {label}", "callback_data": f"del:{label}"}])
    kb.append([{"text": "🧹 Очистить список", "callback_data": "clear:today"}])
    kb.extend(finished_buttons)
    await send_message(chat_id, "\n".join(lines) + "\n\nХотите исправить список?",
                       reply_markup={"inline_keyboard": kb})

# очень простой транслит для подсказки
def _simple_translit(name: str) -> str:
    repl = {
        "sch": "ш", "sh": "ш", "ch": "ч", "ts": "ц", "ya": "я", "yu": "ю", "yo": "ё",
        "kh": "х", "zh": "ж", "th": "т", "ph": "ф", "ck": "к", "qu": "кв",
    }
    s = name.strip()
    out = ""
    i = 0
    low = s.lower()
    while i < len(s):
        took = False
        for k,v in repl.items():
            if low.startswith(k, i):
                out += v; i += len(k); took = True; break
        if not took:
            ch = s[i]
            out += {
                "a":"а","b":"б","c":"к","d":"д","e":"е","f":"ф","g":"г","h":"х",
                "i":"и","j":"дж","k":"к","l":"л","m":"м","n":"н","o":"о","p":"п",
                "r":"р","s":"с","t":"т","u":"у","v":"в","w":"в","x":"кс","y":"и","z":"з",
                "q":"к",
            }.get(ch.lower(), ch)
            i += 1
    # заглавные по словам
    return " ".join(w[:1].upper() + w[1:] for w in out.split())

def _render_post_from_stats(stats: Dict[str, Any]) -> str:
    # минимальная версия: счёт и длительность. Если статистика есть — расширим.
    ev = stats.get("event") or stats
    home = ((ev.get("homeTeam") or {}).get("name")) or "Игрок A"
    away = ((ev.get("awayTeam") or {}).get("name")) or "Игрок B"
    # Sofascore часто кладёт сеты в "homeScore"/"awayScore", либо в "changes"
    hs, as_ = ev.get("homeScore", {}), ev.get("awayScore", {})
    sets = []
    for k in ("period1","period2","period3","period4","period5"):
        if k in hs or k in as_:
            sets.append(f"{hs.get(k,0)}:{as_.get(k,0)}")
    score_line = " ".join(sets) if sets else "счёт недоступен"
    dur = ev.get("time", {}).get("played") or ev.get("length")
    dur_text = f"\nВремя: {dur}" if dur else ""

    # если где-то есть подробные статистики — попробуем собрать
    lines = [f"<b>{home} — {away}</b>", f"Счёт: {score_line}{dur_text}"]
    # (детальные метрики заполним, когда источник стабильно отдаст статистику)
    return "\n".join(lines)

# маппинг путей (не ловить 404)
@app.post("/")
async def webhook_root(req: Request):
    return await _handle(req)

@app.post("/api/webhook")
async def webhook_abs(req: Request):
    return await _handle(req)
