from __future__ import annotations

import os
import datetime as dt
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx

from db_pg import (
    ensure_schema,
    get_tz, set_tz,
    add_watch, remove_watch, list_today,
    ru_name_for, set_alias,
    set_pending_alias, consume_pending_alias,
    get_events_cache,
    save_ui_state, load_ui_state,
)
from providers.sofascore import classify_tier, pretty_tournament_name, pick_players

app = FastAPI(title="telegram-webhook")
handler = app

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/London")


def _tg_api(m: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{m}"


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


# --------- helpers ---------
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

AUTO_RU = {
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
    return AUTO_RU.get(en_full, en_full)


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


def _ev_date_in_tz(ev: Dict[str, Any], tz: ZoneInfo) -> Optional[dt.date]:
    ts = ev.get("startTimestamp")
    if not isinstance(ts, int):
        return None
    return dt.datetime.fromtimestamp(ts, tz).date()


def _events_today(chat_id: int) -> List[Dict[str, Any]]:
    tz = _tz_for(chat_id)
    ds = _today(chat_id)
    data = get_events_cache(ds) or {}
    evs = []
    for ev in (data.get("events") or []):
        d = _ev_date_in_tz(ev, tz)
        if d == ds:
            evs.append(ev)
    return evs


def _slug(s: str) -> str:
    # простая «слагизация» для callback_data
    s = (s or "").strip().lower()
    for ch in " /\\,.:;!?'\"()[]{}+&":
        s = s.replace(ch, "-")
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "t"


def _format_time_left(ev: Dict[str, Any], tz: ZoneInfo) -> str:
    ts = ev.get("startTimestamp")
    if not isinstance(ts, int):
        return ""
    start = dt.datetime.fromtimestamp(ts, tz)
    now = dt.datetime.now(tz)
    status = (ev.get("status") or {}).get("type") or ""
    if status in ("finished", "afterextra", "ap", "ft"):
        return "завершён"
    if status in ("inprogress", "inprogress_penalties", "inprogress_extra_time"):
        return "идёт"
    if start > now:
        delta = start - now
        h = delta.seconds // 3600 + delta.days * 24
        m = (delta.seconds % 3600) // 60
        when = start.strftime("%H:%M")
        if h <= 0 and m > 0:
            return f"{when} (через {m} м)"
        if h > 0 and m == 0:
            return f"{when} (через {h} ч)"
        if h > 0 and m > 0:
            return f"{when} (через {h} ч {m} м)"
        return when
    return "ожидаем подтверждения"


# --------- UI blocks ---------
async def _send_watches_list(chat_id: int):
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        await tg_send_message(
            chat_id,
            "Сегодня (%s):\n—\n\nДобавьте игроков: /watch Rublev, Musetti" % day.isoformat()
        )
        return
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    await tg_send_message(
        chat_id,
        "Сегодня (%s):\n%s\n\nХотите исправить список?" % (day.isoformat(), _format_list_with_ru(arr)),
        reply_markup={"inline_keyboard": buttons}
    )


async def _send_tier_menu(chat_id: int):
    evs = _events_today(chat_id)
    if not evs:
        await tg_send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\nКэш пополнится GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: /watch Rublev, Musetti."
        )
        return

    by_tier: Dict[str, int] = {"ATP": 0, "Challengers": 0, "Другие": 0}
    seen_tournaments: Dict[str, set] = {"ATP": set(), "Challengers": set(), "Другие": set()}

    for ev in evs:
        tier = classify_tier(ev)
        tname = pretty_tournament_name(ev)
        seen_tournaments[tier].add(tname)

    for tier, s in seen_tournaments.items():
        by_tier[tier] = len(s)

    text = (
        f"Турниры сегодня ({_today(chat_id).isoformat()}):\n"
        f"ATP — {by_tier['ATP']} турниров\n"
        f"Challengers — {by_tier['Challengers']} турниров\n"
        f"Другие — {by_tier['Другие']} турниров\n\n"
        "Выберите раздел:"
    )
    kb = [[{"text": "ATP", "callback_data": "tier:ATP"}],
          [{"text": "Challengers", "callback_data": "tier:Challengers"}],
          [{"text": "Другие", "callback_data": "tier:Другие"}]]
    await tg_send_message(chat_id, text, reply_markup={"inline_keyboard": kb})


async def _send_tournaments(chat_id: int, tier: str):
    evs = _events_today(chat_id)
    tier_evs = [e for e in evs if classify_tier(e) == tier]
    if not tier_evs:
        await tg_send_message(chat_id, f"В разделе «{tier}» сегодня турниров не найдено.")
        return

    tournaments: Dict[str, str] = {}  # slug -> name
    for ev in tier_evs:
        name = pretty_tournament_name(ev)
        slug = _slug(name)
        tournaments[slug] = name

    save_ui_state(chat_id, {"tier": tier, "tournaments": tournaments})

    rows = [[{"text": nm, "callback_data": f"t:{sl}"}] for sl, nm in tournaments.items()]
    rows.append([{"text": "⬅️ Назад", "callback_data": "back:tiers"}])
    await tg_send_message(chat_id, f"{tier}: выберите турнир", reply_markup={"inline_keyboard": rows})


async def _send_players_for_tournament(chat_id: int, slug: str):
    state = load_ui_state(chat_id)
    tournaments = (state.get("tournaments") or {})
    name = tournaments.get(slug)
    if not name:
        await tg_send_message(chat_id, "Не удалось найти турнир. Попробуйте снова /start.")
        return

    tz = _tz_for(chat_id)
    evs = [e for e in _events_today(chat_id) if _slug(pretty_tournament_name(e)) == slug]
    if not evs:
        await tg_send_message(chat_id, f"В турнире «{name}» сегодня нет матчей.")
        return

    # собираем игроков и матчи
    per_player: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evs:
        for p in pick_players(ev):
            per_player.setdefault(p, []).append(ev)

    # текст + кнопки для add watch
    lines = [f"{name}: участники сегодня"]
    btns: List[List[Dict[str, str]]] = []
    for p, lst in sorted(per_player.items()):
        # время ближайшего матча, статус
        next_ev = sorted(lst, key=lambda e: e.get("startTimestamp") or 0)[0]
        info = _format_time_left(next_ev, tz)
        lines.append(f"• {p} — {info}")
        btns.append([{"text": f"Следить: {p}", "callback_data": f"w:{p}"}])

    btns.append([{"text": "Добавить всех", "callback_data": f"w_all:{slug}"}])
    btns.append([{"text": "⬅️ Назад", "callback_data": "back:tournaments"}])

    await tg_send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": btns})


# --------- commands ---------
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


# --------- routing ---------
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
        data = (cb.get("data") or "").strip()

        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            await tg_answer_callback_query(cb.get("id"), "Удалено" if removed else "Не найдено")
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True, "action": "rm"})

        if data.startswith("tier:"):
            tier = data.split(":", 1)[1]
            await tg_answer_callback_query(cb.get("id"))
            await _send_tournaments(chat_id, tier)
            return JSONResponse({"ok": True, "action": "tier"})

        if data.startswith("t:"):
            slug = data.split(":", 1)[1]
            await tg_answer_callback_query(cb.get("id"))
            await _send_players_for_tournament(chat_id, slug)
            return JSONResponse({"ok": True, "action": "tournament"})

        if data.startswith("w_all:"):
            slug = data.split(":", 1)[1]
            await tg_answer_callback_query(cb.get("id"))
            # добавить всех игроков турнира
            tz = _tz_for(chat_id)
            today = _today(chat_id)
            from providers.sofascore import pretty_tournament_name
            evs = [e for e in _events_today(chat_id) if _slug(pretty_tournament_name(e)) == slug]
            names = set()
            for ev in evs:
                for p in pick_players(ev):
                    names.add(p)
            for n in names:
                add_watch(chat_id, n, today)
            await tg_send_message(chat_id, f"Добавил {len(names)} игроков в список слежения на сегодня.")
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True, "action": "w_all"})

        if data.startswith("w:"):
            name = data.split(":", 1)[1]
            # нормализуем + ru-алиас при необходимости
            pair = ru_name_for(name)
            if pair is None or (pair and pair[0] == "" and pair[1] is False):
                set_pending_alias(chat_id, name)
                await tg_answer_callback_query(cb.get("id"))
                await tg_send_message(
                    chat_id,
                    f"Как записать *{name}* по-русски?\n\n"
                    f"Вариант: _{_auto_ru_guess(name)}_\n"
                    f"Или пришлите свой вариант.",
                    parse_mode="Markdown"
                )
                return JSONResponse({"ok": True, "action": "w_pending"})
            add_watch(chat_id, name, _today(chat_id))
            await tg_answer_callback_query(cb.get("id"), "Добавлено")
            await _send_watches_list(chat_id)
            return JSONResponse({"ok": True, "action": "w_one"})

        if data == "back:tournaments":
            st = load_ui_state(chat_id)
            await tg_answer_callback_query(cb.get("id"))
            await _send_tournaments(chat_id, st.get("tier") or "ATP")
            return JSONResponse({"ok": True, "action": "back_tournaments"})

        if data == "back:tiers":
            await tg_answer_callback_query(cb.get("id"))
            await _send_tier_menu(chat_id)
            return JSONResponse({"ok": True, "action": "back_tiers"})

        await tg_answer_callback_query(cb.get("id"))
        return JSONResponse({"ok": True, "action": "noop"})

    # message
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
            await _send_tier_menu(chat_id)
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
