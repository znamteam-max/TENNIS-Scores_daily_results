# api/webhook.py — чистый ASGI без fastapi/httpx/psycopg

from __future__ import annotations

import os, json, datetime as dt, random, asyncio
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import urllib.request, urllib.error

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/London")

# ----- Пытаемся подключить db_pg, но это НЕ обязательно -----
_HAVE_DB = True
try:
    from db_pg import (  # type: ignore
        ensure_schema,
        get_tz, set_tz,
        add_watch, remove_watch, list_today,
        ru_name_for, set_alias,
        set_pending_alias, consume_pending_alias,
        get_events_cache,
    )
except Exception:
    _HAVE_DB = False

    # безопасные заглушки, чтобы код не падал
    def ensure_schema() -> None: ...
    def get_tz(chat_id: int) -> Optional[str]: return None
    def set_tz(chat_id: int, tz: str) -> None: ...
    def add_watch(chat_id: int, name_en: str, day: dt.date) -> None: ...
    def remove_watch(chat_id: int, day: dt.date, name_en: str) -> bool: return False
    def list_today(chat_id: int, day: dt.date) -> List[str]: return []
    def ru_name_for(en_full: str) -> Optional[Tuple[str, bool]]: return None
    def set_alias(en_full: str, ru: str) -> None: ...
    def set_pending_alias(chat_id: int, en_full: str) -> None: ...
    def consume_pending_alias(chat_id: int) -> Optional[str]: return None
    def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]: return None

# ================== ASGI utils ==================
async def _send_json(send, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [[b"content-type", b"application/json; charset=utf-8"]]})
    await send({"type": "http.response.body", "body": body})

async def _read_body(receive) -> bytes:
    chunks, more = [], True
    while more:
        ev = await receive()
        chunk = ev.get("body", b"")
        if chunk: chunks.append(chunk)
        more = ev.get("more_body", False)
    return b"".join(chunks)

# ================== Telegram ==================
def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _http_post_json(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.URLError:
        pass  # не валим функцию

async def tg_send_message(chat_id: int, text: str, **kwargs) -> None:
    if not BOT_TOKEN: return
    payload = {"chat_id": chat_id, "text": text}
    payload.update(kwargs)
    _http_post_json(_tg_api("sendMessage"), payload)

async def tg_answer_callback_query(cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
    if not BOT_TOKEN: return
    payload = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text: payload["text"] = text
    _http_post_json(_tg_api("answerCallbackQuery"), payload)

# ================== helpers ==================
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
        pair = ru_name_for(it)
        if pair is None:
            lines.append(f"• {it}")
        else:
            ru, known = pair
            lines.append(f"• {ru if (ru and known) else it}")
    return "\n".join(lines) if lines else "—"

# ================== Events (SofaScore) ==================
_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
]
_BASES = ["https://api.sofascore.com/api/v1", "https://www.sofascore.com/api/v1"]

def _fetch_json(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json, text/plain, */*",
        "User-Agent": random.choice(_UAS),
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", "ignore")
            return json.loads(txt)
    except Exception:
        return None

def _events_today(ds: dt.date) -> List[dict]:
    # 1) кэш из БД (если есть)
    data = get_events_cache(ds) or {}
    events = (data.get("events") or data.get("list") or []) if isinstance(data, dict) else []
    if events: return events
    # 2) прямой фетч без зависимостей (может дать 403 — тогда пусто)
    paths = [f"/sport/tennis/scheduled-events/{ds.isoformat()}",
             f"/sport/tennis/events/{ds.isoformat()}"]
    for base in _BASES:
        for p in paths:
            d = _fetch_json(base + p)
            if isinstance(d, dict):
                evs = d.get("events") or d.get("list") or []
                if evs: return evs
            asyncio.sleep(0.25)
    live = _fetch_json(_BASES[0] + "/sport/tennis/events/live")
    if isinstance(live, dict):
        return live.get("events") or live.get("list") or []
    return []

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

def _tname(ev: Dict[str, Any]) -> str:
    t = (ev or {}).get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return ut.get("name") or t.get("name") or "Турнир"

def _players(ev: Dict[str, Any]) -> Tuple[str, str]:
    a = ((ev or {}).get("homeTeam") or {}).get("name") or "Player A"
    b = ((ev or {}).get("awayTeam") or {}).get("name") or "Player B"
    return a, b

def _index_by_category(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    by = {"ATP": {}, "Challengers": {}, "Другие": {}}
    for ev in events:
        cat = _classify(ev)
        tn = _tname(ev)
        by.setdefault(cat, {}).setdefault(tn, []).append(ev)
    return by

# ================== UI blocks ==================
async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send_message(chat_id, f"Сегодня ({day.isoformat()}):\n—\n\nДобавьте игроков: /watch Rublev, Musetti")
        return
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send_message(
        chat_id,
        f"Сегодня ({day.isoformat()}):\n{_format_list_with_ru(arr)}\n\nХотите исправить список?",
        reply_markup={"inline_keyboard": buttons}
    )

async def _send_start(chat_id: int):
    ds = _today(chat_id)
    events = _events_today(ds)
    if not events:
        await tg_send_message(chat_id,
            "Расписание на сегодня пока недоступно.\n"
            "Можно добавить игроков вручную: /watch Sinner")
        return
    by = _index_by_category(events)
    counts = {k: sum(len(v) for v in by.get(k, {}).values()) for k in ("ATP","Challengers","Другие")}
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
    by = _index_by_category(_events_today(ds))
    tours = sorted((by.get(category) or {}).keys())
    if not tours:
        await tg_send_message(chat_id, f"В категории {category} турниров нет.")
        return
    kb = [[{"text": name[:64], "callback_data": f"tour:{category}:{name[:64]}"}] for name in tours[:25]]
    await tg_send_message(chat_id, f"{category}: выберите турнир", reply_markup={"inline_keyboard": kb})

async def _send_players_for_tournament(chat_id: int, category: str, tour_name: str):
    ds = _today(chat_id)
    by = _index_by_category(_events_today(ds))
    evs = (by.get(category) or {}).get(tour_name) or []
    if not evs:
        await tg_send_message(chat_id, "Нет матчей.")
        return
    kb, lines = [], [f"{tour_name} — матчи сегодня:"]
    for ev in evs[:40]:
        a, b = _players(ev)
        title = f"{a} vs {b}"
        lines.append(f"• {title}")
        kb.append([{"text": f"Следить: {a}", "callback_data": f"watch:{a}"},
                   {"text": f"Следить: {b}", "callback_data": f"watch:{b}"}])
        st = (ev.get("status") or {}).get("type") or ""
        if str(st).lower() in ("finished","ended","after overtime"):
            kb.append([{"text": "Сформировать пост", "callback_data": f"mkpost:{a}::{b}"}])
    await tg_send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": kb})

# ================== commands ==================
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
                f"Как записать *{en_full}* по-русски?\n\nВариант: _{suggestion}_\nИли пришлите свой вариант одним сообщением.",
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
                f"Как записать *{en_full}* по-русски?\n\nВариант: _{suggestion}_\nИли пришлите свой вариант одним сообщением.",
                parse_mode="Markdown"
            )
    parts = []
    if added: parts.append("Добавил:\n" + "\n".join(f"• {x}" for x in added))
    if asked: parts.append("\nЖду русскую запись для:\n" + "\n".join(f"• {x}" for x in asked))
    if parts:
        parts.append("\n/list — показать список на сегодня")
        await tg_send_message(chat_id, "\n".join(parts))

async def _handle_text_message(chat_id: int, text: str) -> bool:
    if text.startswith("/"): return False
    pending = consume_pending_alias(chat_id)
    if not pending: return False
    ru = text.strip()
    if not ru:
        await tg_send_message(chat_id, "Пустой ответ. Пришлите, как записать имя по-русски.")
        return True
    set_alias(pending, ru)
    add_watch(chat_id, pending, _today(chat_id))
    await tg_send_message(chat_id, f"Сохранил: *{ru}* (EN: {pending}).\n/list — показать список", parse_mode="Markdown")
    return True

# ================== ASGI entry ==================
async def app(scope, receive, send):
    if scope["type"] != "http":
        await _send_json(send, 200, {"ok": True, "note": "not http"})
        return

    method = scope.get("method") or "GET"

    # GET — health
    if method == "GET":
        mode = "db+cache" if _HAVE_DB else "no-packages"
        await _send_json(send, 200, {"ok": True, "service": "webhook", "path": "/api/webhook", "mode": mode})
        return

    # POST — Telegram webhook
    if method != "POST":
        await _send_json(send, 405, {"ok": False, "error": "method not allowed"})
        return

    # секрет от Телеграм, если задан
    if WEBHOOK_SECRET:
        hdrs = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if hdrs.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            await _send_json(send, 403, {"error": "forbidden"})
            return

    try:
        ensure_schema()
    except Exception:
        pass

    raw = await _read_body(receive)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    # callback_query
    cb = payload.get("callback_query")
    if cb:
        chat_id = cb["message"]["chat"]["id"]
        data = (cb.get("data") or "")
        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            await tg_answer_callback_query(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
            await _send_json(send, 200, {"ok": True, "action": "rm"})
            return
        if data.startswith("cat:"):
            _, cat = data.split(":", 1)
            await tg_answer_callback_query(cb.get("id"))
            await _send_tournaments(chat_id, cat)
            await _send_json(send, 200, {"ok": True, "action": "cat"})
            return
        if data.startswith("tour:"):
            _, cat, tour = data.split(":", 2)
            await tg_answer_callback_query(cb.get("id"))
            await _send_players_for_tournament(chat_id, cat, tour)
            await _send_json(send, 200, {"ok": True, "action": "tour"})
            return
        if data.startswith("watch:"):
            _, nm = data.split(":", 1)
            await tg_answer_callback_query(cb.get("id"))
            await _handle_watch(chat_id, nm)
            await _send_json(send, 200, {"ok": True, "action": "watch"})
            return
        if data.startswith("mkpost:"):
            await tg_answer_callback_query(cb.get("id"))
            await tg_send_message(chat_id, "Шаблон поста: *Матч завершён.* Счёт: 6-4 4-6 7-6(5).", parse_mode="Markdown")
            await _send_json(send, 200, {"ok": True, "action": "mkpost"})
            return
        await tg_answer_callback_query(cb.get("id"))
        await _send_json(send, 200, {"ok": True, "action": "noop"})
        return

    # обычное сообщение
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        await _send_json(send, 200, {"ok": True, "ignored": True})
        return

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    if await _handle_text_message(chat_id, text):
        await _send_json(send, 200, {"ok": True})
        return

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("start", "menu"):
            await _send_start(chat_id); await _send_json(send, 200, {"ok": True}); return
        if cmd == "list":
            await _send_watches_list(chat_id); await _send_json(send, 200, {"ok": True}); return
        if cmd == "watch":
            await _handle_watch(chat_id, arg); await _send_json(send, 200, {"ok": True}); return
        if cmd == "settz":
            tz = (arg or "").strip()
            if not tz:
                curr = get_tz(chat_id)
                await tg_send_message(chat_id, f"Текущая TZ: {curr}. Пример: /settz Europe/Moscow")
            else:
                try:
                    ZoneInfo(tz); set_tz(chat_id, tz)
                    await tg_send_message(chat_id, f"TZ обновлена: {tz}")
                except Exception:
                    await tg_send_message(chat_id, "Некорректная TZ. Пример: Europe/Moscow")
            await _send_json(send, 200, {"ok": True}); return
        await tg_send_message(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")
        await _send_json(send, 200, {"ok": True}); return

    await _send_json(send, 200, {"ok": True})

# для Vercel
handler = app
