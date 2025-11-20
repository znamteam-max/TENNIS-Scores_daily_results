# api/webhook.py — ASGI webhook с категориями турниров и быстрым watch
from __future__ import annotations
import os, json, datetime as dt, math, urllib.parse
from typing import Optional, List, Dict, Any, Tuple
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

# ---------------- Telegram I/O ----------------

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

# ---------------- helpers ----------------

def _canon_en(token: str) -> str:
    KNOWN_EN = {
        "sinner": "Jannik Sinner",
        "зверев": "Alexander Zverev",
        "zverev": "Alexander Zverev",
        "rublev": "Andrey Rublev",
        "рублев": "Andrey Rublev",
        "medvedev": "Daniil Medvedev",
        "джокович": "Novak Djokovic",
        "djokovic": "Novak Djokovic",
        "alcaraz": "Carlos Alcaraz",
        "musetti": "Lorenzo Musetti",
        "de minaur": "Alex de Minaur",
        "деминор": "Alex de Minaur",
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

def _now(chat_id: int) -> dt.datetime:
    return dt.datetime.now(_tz_for(chat_id))

def _today(chat_id: int) -> dt.date:
    return _now(chat_id).date()

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
        if pair and pair[0] and pair[1]:
            lines.append(f"• {pair[0]}")
        else:
            lines.append(f"• {it}")
    return "\n".join(lines) if lines else "—"

# --- Sofascore event utils (robust keys) ---

def _get(ev: Dict[str, Any], *keys, default=None):
    cur = ev
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default

def _start_ts(ev: Dict[str, Any]) -> Optional[int]:
    for k in ("startTimestamp", "startTime", "start_ts"):
        ts = ev.get(k)
        if isinstance(ts, (int, float)):
            return int(ts)
    tblock = ev.get("time") or ev.get("start") or {}
    if isinstance(tblock, dict):
        for k in ("timestamp", "scheduled", "startTimestamp"):
            ts = tblock.get(k)
            if isinstance(ts, (int, float)):
                return int(ts)
    return None

def _players(ev: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    cand_pairs = [
        ("homePlayer", "awayPlayer"),
        ("homeCompetitor", "awayCompetitor"),
        ("homeTeam", "awayTeam"),
        ("player1", "player2"),
    ]
    def name_of(x):
        if not isinstance(x, dict):
            return None
        return x.get("name") or x.get("shortName") or x.get("slug") or x.get("code")
    for a, b in cand_pairs:
        na, nb = name_of(ev.get(a)), name_of(ev.get(b))
        if na and nb:
            return na, nb
    parts = ev.get("participants") or ev.get("competitors")
    if isinstance(parts, list) and len(parts) >= 2:
        na = parts[0].get("name")
        nb = parts[1].get("name")
        if na and nb:
            return na, nb
    return None, None

def _tournament_id_name(ev: Dict[str, Any]) -> Tuple[str, str]:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    tid = str(ut.get("id") or t.get("id") or ev.get("id") or "")
    name = (ut.get("name") or t.get("name") or "Турнир")
    return tid or name, name

def _category_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat_name = (_get(ut, "category", "name") or _get(t, "category", "name") or "").lower()
    tname = (ut.get("name") or t.get("name") or "").lower()
    if "challenger" in cat_name or "challenger" in tname:
        return "Challengers"
    if "atp" in cat_name or "atp" in tname:
        return "ATP"
    return "Other"

def _catalog(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    cats = {"ATP": {}, "Challengers": {}, "Other": {}}
    for ev in events:
        cat = _category_name(ev)
        tid, tname = _tournament_id_name(ev)
        bucket = cats.setdefault(cat, {})
        bucket.setdefault(tid, {"name": tname, "events": []})["events"].append(ev)
    return cats

def _fmt_eta(chat_id: int, ev: Dict[str, Any]) -> str:
    ts = _start_ts(ev)
    if not ts:
        return "время не указано"
    start = dt.datetime.fromtimestamp(int(ts), _tz_for(chat_id))
    delta = start - _now(chat_id)
    sec = int(delta.total_seconds())
    if sec <= -60:
        return "уже сыгран / идёт"
    if -60 < sec < 60:
        return "сейчас"
    mins = sec // 60
    h, m = mins // 60, mins % 60
    if h:
        return f"через {h}ч {m:02d}м"
    return f"через {m}м"

# ---------------- UI blocks ----------------

async def _send_categories(chat_id: int, ds: dt.date, events: List[Dict[str, Any]]):
    cats = _catalog(events)
    cnt_atp = len(cats["ATP"])
    cnt_ch  = len(cats["Challengers"])
    cnt_oth = len(cats["Other"])
    txt = (
        f"Турниры сегодня ({ds.isoformat()}):\n\n"
        f"ATP — {cnt_atp}\n"
        f"Challengers — {cnt_ch}\n"
        f"Другие — {cnt_oth}\n\n"
        f"Выберите категорию:"
    )
    kb = [[{"text": "ATP", "callback_data": "cat:ATP"}],
          [{"text": "Challengers", "callback_data": "cat:Challengers"}],
          [{"text": "Другие", "callback_data": "cat:Other"}]]
    await tg_send(chat_id, txt, reply_markup={"inline_keyboard": kb})

async def _send_tournaments(chat_id: int, category: str, ds: dt.date, events: List[Dict[str, Any]]):
    cats = _catalog(events)
    bucket = cats.get(category) or {}
    if not bucket:
        await tg_send(chat_id, f"В категории {category} турниров на сегодня нет.")
        return
    lines = [f"{category} — турниры ({ds.isoformat()}):"]
    kb = []
    for tid, info in sorted(bucket.items(), key=lambda kv: kv[1]["name"]):
        nm = info["name"]; cnt = len(info["events"])
        lines.append(f"• {nm} — {cnt} матч(ей)")
        kb.append([{"text": f"{nm} ({cnt})", "callback_data": f"t:{category}:{tid}"}])
    await tg_send(chat_id, "\n".join(lines) + "\n\nВыберите турнир:", reply_markup={"inline_keyboard": kb})

def _uniq(seq: List[str]) -> List[str]:
    seen = set(); out = []
    for s in seq:
        if s and s not in seen:
            out.append(s); seen.add(s)
    return out

async def _send_players_for_tournament(chat_id: int, category: str, tid: str, ds: dt.date, events: List[Dict[str, Any]]):
    cats = _catalog(events)
    info = (cats.get(category) or {}).get(tid)
    if not info:
        await tg_send(chat_id, "Турнир не найден/устарел. Нажмите /start.")
        return
    evs = info["events"]
    # соберём игроков
    players: List[str] = []
    lines = [f"{info['name']} — игроки сегодня ({ds.isoformat()}):"]
    for ev in evs:
        p1, p2 = _players(ev)
        if p1: players.append(p1)
        if p2: players.append(p2)
    players = _uniq(players)
    if not players:
        await tg_send(chat_id, "Список игроков пуст.")
        return

    # кнопки на подписку (по 2 в ряд)
    kb_rows = []
    row = []
    for p in players[:24]:  # ограничим клавиатуру
        lab = f"+ {p}"
        row.append({"text": lab, "callback_data": f"w:{p}"})
        if len(row) == 2:
            kb_rows.append(row); row = []
    if row: kb_rows.append(row)

    # текст + предложение
    txt = "\n".join(["• " + x for x in players])
    txt += "\n\nНажмите, чтобы подписаться на игрока на сегодня."

    await tg_send(chat_id, txt, reply_markup={"inline_keyboard": kb_rows})

# ---------------- command handlers ----------------

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
        if pair is None or (pair and not pair[0] and not pair[1]):
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

# ---------------- dialogs ----------------

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
    # если кэша нет — объясняем
    if not data:
        await tg_send(
            chat_id,
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
    await _send_categories(chat_id, ds, events)

# ---------------- ASGI entry ----------------

async def app(scope, receive, send):
    if scope.get("type") != "http":
        return

    method = scope.get("method")
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

    # GET — всегда ок (для браузера/health)
    if method == "GET":
        await _json(send, {"ok": True, "service": "webhook", "path": "/api/webhook"})
        return

    # Только POST от Telegram проверяем секрет
    if method == "POST" and WEBHOOK_SECRET:
        if headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
            await _json(send, {"error": "forbidden"}, 403)
            return

    if method != "POST":
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
            await _json(send, {"ok": True, "action": "rm"}); return

        if data.startswith("cat:"):
            cat = data.split(":", 1)[1]
            ds = _today(chat_id)
            cache = get_events_cache(ds) or {}
            events = cache.get("events") or cache.get("list") or []
            await tg_answer(cb.get("id"))
            await _send_tournaments(chat_id, cat, ds, events)
            await _json(send, {"ok": True, "action": "cat"}); return

        if data.startswith("t:"):
            # t:<Category>:<TournamentId>
            _, cat, tid = (data.split(":", 2) + ["", ""])[0:3]
            ds = _today(chat_id)
            cache = get_events_cache(ds) or {}
            events = cache.get("events") or cache.get("list") or []
            await tg_answer(cb.get("id"))
            await _send_players_for_tournament(chat_id, cat, tid, ds, events)
            await _json(send, {"ok": True, "action": "t"}); return

        if data.startswith("w:"):
            name = data[2:]
            # Повторно используем механику /watch для автоалиаса
            await tg_answer(cb.get("id"))
            await _handle_watch(chat_id, name)
            await _json(send, {"ok": True, "action": "w"}); return

        await tg_answer(cb.get("id"))
        await _json(send, {"ok": True, "action": "noop"}); return

    # message
    msg = payload.get("message") or payload.get("edited_message")
    if not msg:
        await _json(send, {"ok": True, "ignored": True}); return

    chat_id = msg["chat"]["id"]
    text = msg.get("text") or ""

    if await _handle_text_alias(chat_id, text):
        await _json(send, {"ok": True}); return

    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("start", "menu"):
            await _send_start(chat_id)
            await _json(send, {"ok": True}); return

        if cmd == "list":
            await _send_watches_list(chat_id)
            await _json(send, {"ok": True}); return

        if cmd == "watch":
            await _handle_watch(chat_id, arg)
            await _json(send, {"ok": True}); return

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
            await _json(send, {"ok": True}); return

        await tg_send(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")
        await _json(send, {"ok": True}); return

    await _json(send, {"ok": True})

# ---- tiny JSON responder ----
async def _json(send, obj: Dict[str, Any], status: int = 200):
    body = json.dumps(obj).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
