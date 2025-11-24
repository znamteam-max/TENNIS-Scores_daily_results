# api/webhook.py
# WSGI-версия: без FastAPI/httpx. Только stdlib + psycopg через db_pg.py

from __future__ import annotations
import os, json, datetime as dt, time, urllib.request, urllib.error
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from db_pg import (
    ensure_schema,
    get_tz, set_tz,
    add_watch, remove_watch, list_today,
    ru_name_for, set_alias,
    set_pending_alias, consume_pending_alias,
    get_events_cache,
)

# --- ENV
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")

# --- Telegram I/O (stdlib)
def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _post_json(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError:
        pass

def tg_send_message(chat_id: int, text: str, **kwargs) -> None:
    if not BOT_TOKEN:
        return
    payload = {"chat_id": chat_id, "text": text}
    payload.update(kwargs)
    _post_json(_tg_api("sendMessage"), payload)

def tg_answer_callback_query(cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
    if not BOT_TOKEN:
        return
    payload = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    _post_json(_tg_api("answerCallbackQuery"), payload)

# --- helpers
def _tz_for(chat_id: int) -> ZoneInfo:
    try:
        return ZoneInfo(get_tz(chat_id) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)

def _now(chat_id: int) -> dt.datetime:
    return dt.datetime.now(_tz_for(chat_id))

def _today(chat_id: int) -> dt.date:
    return _now(chat_id).date()

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
    t = (token or "").strip()
    k = " ".join(t.lower().split())
    return KNOWN_EN.get(k, t)

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

def _fmt_hm(dtobj: dt.datetime) -> str:
    return dtobj.strftime("%H:%M")

def _fmt_tdelta(now: dt.datetime, start_ts: int) -> str:
    try:
        start = dt.datetime.fromtimestamp(start_ts, tz=now.tzinfo)
        left = start - now
        if left.total_seconds() <= 0:
            return "уже начался"
        mins = int(left.total_seconds() // 60)
        h, m = divmod(mins, 60)
        if h:
            return f"через {h}ч {m:02d}м"
        return f"через {m}м"
    except Exception:
        return ""

def _event_side_name(ev: Dict[str, Any], side: str) -> Optional[str]:
    # пробуем разные ключи, т.к. у Sofascore структура может отличаться
    cand = []
    if side == "home":
        cand = ["homePlayer", "homeCompetitor", "homeTeam", "home"]
    else:
        cand = ["awayPlayer", "awayCompetitor", "awayTeam", "away"]
    for k in cand:
        obj = ev.get(k)
        if isinstance(obj, dict):
            return obj.get("name") or obj.get("shortName")
    # иногда в 'competitors' массив
    comps = ev.get("competitors")
    if isinstance(comps, list) and len(comps) == 2:
        idx = 0 if side == "home" else 1
        nm = (comps[idx] or {}).get("name")
        if nm:
            return nm
    return None

def _classify_category(ev: Dict[str, Any]) -> str:
    # Грубая эвристика: по имени турнира и uniqueTournament.category / name
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    name = (ut.get("name") or t.get("name") or "").lower()

    if "challenger" in name:
        return "Challengers"
    # ITF / UTR / Кубки и т.п. — в "Другие"
    if any(x in name for x in ["itf", "utr", "davis", "cup", "united cup", "futures"]):
        return "Другие"
    # всё остальное трактуем как ATP
    return "ATP"

def _tournament_key(ev: Dict[str, Any]) -> Tuple[str, int]:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    name = ut.get("name") or t.get("name") or "Турнир"
    uid = ut.get("id") or t.get("id") or 0
    return (name, int(uid))

def _gather_by_category(events: List[Dict[str, Any]]) -> Dict[str, Dict[int, Dict[str, Any]]]:
    # return: { category: { ut_id: {"name":..., "events":[...] } } }
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for ev in events:
        cat = _classify_category(ev)
        name, uid = _tournament_key(ev)
        out.setdefault(cat, {}).setdefault(uid, {"name": name, "events": []})["events"].append(ev)
    return out

def _markup_rows(buttons: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": buttons}

# --- UI blocks
def _send_start(chat_id: int) -> None:
    ds = _today(chat_id)
    data = get_events_cache(ds)
    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []

    if not events:
        tg_send_message(
            chat_id,
            "На сегодня список турниров пуст.\n"
            "Кэш обновляется воркером каждые 30 минут.\n\n"
            "Добавьте игроков вручную: /watch Rublev, Musetti."
        )
        return

    byc = _gather_by_category(events)
    n_atp = len(byc.get("ATP", {}))
    n_ch  = len(byc.get("Challengers", {}))
    n_oth = len(byc.get("Другие", {}))

    text = (
        f"Турниры сегодня ({ds.isoformat()}):\n"
        f"• ATP — {n_atp}\n"
        f"• Challengers — {n_ch}\n"
        f"• Другие — {n_oth}\n\n"
        f"Выберите категорию:"
    )
    kb = [
        [{"text": f"ATP — {n_atp}", "callback_data": "cat:ATP"}],
        [{"text": f"Challengers — {n_ch}", "callback_data": "cat:Challengers"}],
        [{"text": f"Другие — {n_oth}", "callback_data": "cat:Другие"}],
    ]
    tg_send_message(chat_id, text, reply_markup=_markup_rows(kb))

def _send_list(chat_id: int) -> None:
    day = _today(chat_id)
    arr = list_today(chat_id, day)
    if not arr:
        tg_send_message(chat_id, f"Сегодня ({day.isoformat()}):\n—\n\nДобавьте игроков: /watch Rublev, Musetti")
        return
    # кнопки «удалить»
    buttons = [[{"text": f"Удалить {it}", "callback_data": f"rm:{it}"}] for it in arr]
    # + подсказка про исправление списка
    # RU-прослойка
    lines = []
    for it in arr:
        pair = ru_name_for(it)
        ru = None
        known = False
        if pair:
            ru, known = pair
        lines.append(f"• {ru if (ru and known) else it}")
    tg_send_message(
        chat_id,
        f"Сегодня ({day.isoformat()}):\n" + "\n".join(lines) + "\n\nХотите исправить список?",
        reply_markup=_markup_rows(buttons)
    )

def _send_cat(chat_id: int, cat: str) -> None:
    ds = _today(chat_id)
    data = get_events_cache(ds)
    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []
    byc = _gather_by_category(events)
    tmap = byc.get(cat) or {}
    if not tmap:
        tg_send_message(chat_id, f"В категории {cat} сегодня турниров нет.")
        return

    # список турниров -> кнопки
    rows = []
    for uid, item in sorted(tmap.items(), key=lambda x: x[1]["name"]):
        nm = item["name"]
        rows.append([{"text": nm, "callback_data": f"t:{cat}:{uid}"}])
    tg_send_message(chat_id, f"{cat}: выберите турнир", reply_markup=_markup_rows(rows))

def _send_tournament(chat_id: int, cat: str, uid: int) -> None:
    ds = _today(chat_id)
    data = get_events_cache(ds)
    events = []
    if isinstance(data, dict):
        events = data.get("events") or data.get("list") or []

    # найдём турнир по uid
    chosen: List[Dict[str, Any]] = []
    for ev in events:
        _, tuid = _tournament_key(ev)
        if tuid == uid and _classify_category(ev) == cat:
            chosen.append(ev)

    if not chosen:
        tg_send_message(chat_id, "Список матчей пуст.")
        return

    now = _now(chat_id)
    # собираем игроков и время
    lines = []
    seen_players = set()
    for ev in chosen:
        h = _event_side_name(ev, "home") or "—"
        a = _event_side_name(ev, "away") or "—"
        start_ts = ev.get("startTimestamp")
        status = (ev.get("status") or {}).get("type")
        if isinstance(start_ts, int):
            when = dt.datetime.fromtimestamp(start_ts, tz=now.tzinfo)
            left = _fmt_tdelta(now, start_ts)
            tm = _fmt_hm(when)
            if status in ("inprogress", "live"):
                stamp = f"{tm} — LIVE"
            elif status in ("finished", "ft", "ended"):
                stamp = f"{tm} — завершён"
            else:
                stamp = f"{tm} — {left}"
        else:
            stamp = "время уточняется"
        lines.append(f"{h} — {a}  ·  {stamp}")
        # копим игроков
        for nm in (h, a):
            if nm and nm != "—":
                seen_players.add(nm)

    # инлайн «Добавить в отслеживаемые»
    buttons: List[List[Dict[str, str]]] = []
    for nm in sorted(seen_players):
        buttons.append([{"text": f"Добавить: {nm}", "callback_data": f"w:{nm}"}])

    msg = "Матчи сегодня:\n" + "\n".join(lines) + "\n\nНажмите, чтобы отслеживать игрока:"
    tg_send_message(chat_id, msg, reply_markup=_markup_rows(buttons))

# --- commands / watchers
def _handle_watch(chat_id: int, payload: str) -> None:
    names = [x.strip() for x in (payload or "").split(",") if x.strip()]
    if not names:
        tg_send_message(chat_id, "Формат: /watch ИмяИгрока (или несколько через запятую)")
        return
    added, asked = [], []
    today = _today(chat_id)

    for nm in names:
        en_full = _canon_en(nm)
        pair = ru_name_for(en_full)
        if pair is None:
            # вообще нет записи — спросим RU
            suggestion = _auto_ru_guess(en_full)
            set_pending_alias(chat_id, en_full)
            asked.append(en_full)
            tg_send_message(
                chat_id,
                "Как записать *{0}* по-русски?\n\nВариант: _{1}_\nИли пришлите свой вариант одним сообщением.".format(en_full, suggestion),
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
            tg_send_message(
                chat_id,
                "Как записать *{0}* по-русски?\n\nВариант: _{1}_\nИли пришлите свой вариант одним сообщением.".format(en_full, suggestion),
                parse_mode="Markdown"
            )

    parts = []
    if added:
        parts.append("Добавил:\n" + "\n".join(f"• {x}" for x in added))
    if asked:
        parts.append("\nЖду русскую запись для:\n" + "\n".join(f"• {x}" for x in asked))
    if parts:
        parts.append("\n/list — показать список на сегодня")
        tg_send_message(chat_id, "\n".join(parts))

def _handle_text_alias(chat_id: int, text: str) -> bool:
    if text.startswith("/"):
        return False
    pending = consume_pending_alias(chat_id)
    if not pending:
        return False
    ru = (text or "").strip()
    if not ru:
        tg_send_message(chat_id, "Пустой ответ. Пришлите, как записать имя по-русски.")
        return True
    set_alias(pending, ru)
    add_watch(chat_id, pending, _today(chat_id))
    tg_send_message(chat_id, f"Сохранил: *{ru}* (EN: {pending}).\n/list — показать список", parse_mode="Markdown")
    return True

# --- Callback handlers
def _on_cb(chat_id: int, cb_id: str, data: str) -> None:
    try:
        if data.startswith("rm:"):
            name = data[3:]
            removed = remove_watch(chat_id, _today(chat_id), name)
            tg_answer_callback_query(cb_id, "Удалено" if removed else "Не найдено")
            _send_list(chat_id)
            return

        if data.startswith("cat:"):
            tg_answer_callback_query(cb_id)
            cat = data.split(":", 1)[1]
            _send_cat(chat_id, cat)
            return

        if data.startswith("t:"):
            tg_answer_callback_query(cb_id)
            _, cat, uid = data.split(":")
            _send_tournament(chat_id, cat, int(uid))
            return

        if data.startswith("w:"):
            tg_answer_callback_query(cb_id)
            en_full = _canon_en(data[2:])
            pair = ru_name_for(en_full)
            if pair is None or (pair and not pair[0]):
                # спросим RU
                set_pending_alias(chat_id, en_full)
                tg_send_message(chat_id,
                    f"Как записать *{en_full}* по-русски?\n\nВариант: _{_auto_ru_guess(en_full)}_",
                    parse_mode="Markdown")
                return
            ru, known = pair
            add_watch(chat_id, en_full, _today(chat_id))
            tg_send_message(chat_id, f"Добавил: {ru or en_full}\n/list — показать список")
            return

        tg_answer_callback_query(cb_id)
    except Exception as e:
        tg_answer_callback_query(cb_id, "Ошибка")
        tg_send_message(chat_id, f"Ошибка: {e}")

# --- WSGI handler
def handler(environ, start_response):
    if environ.get("REQUEST_METHOD") == "GET":
        body = json.dumps({"ok": True, "service": "webhook", "path": "/api/webhook"}).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    # POST (Telegram webhook)
    if WEBHOOK_SECRET:
        hdr = environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        if hdr != WEBHOOK_SECRET:
            body = json.dumps({"error": "forbidden"}).encode("utf-8")
            start_response("403 Forbidden", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
            return [body]

    ensure_schema()

    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = {}

    # callback_query
    cb = payload.get("callback_query")
    if cb:
        chat_id = cb.get("message", {}).get("chat", {}).get("id")
        cb_id = cb.get("id")
        data = cb.get("data") or ""
        if chat_id and cb_id is not None:
            _on_cb(int(chat_id), cb_id, data)
        body = json.dumps({"ok": True}).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    # message / edited_message
    msg = payload.get("message") or payload.get("edited_message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text") or ""
    if not chat_id:
        body = json.dumps({"ok": True, "ignored": True}).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    chat_id = int(chat_id)

    # если ждём RU-алиас
    if _handle_text_alias(chat_id, text):
        body = json.dumps({"ok": True}).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    # команды
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("start", "menu"):
            _send_start(chat_id)
        elif cmd == "list":
            _send_list(chat_id)
        elif cmd == "watch":
            _handle_watch(chat_id, arg)
        elif cmd == "settz":
            tz = (arg or "").strip()
            if not tz:
                tg_send_message(chat_id, f"Текущая TZ: {get_tz(chat_id)}. Пример: /settz Europe/Moscow")
            else:
                try:
                    ZoneInfo(tz)
                    set_tz(chat_id, tz)
                    tg_send_message(chat_id, f"TZ обновлена: {tz}")
                except Exception:
                    tg_send_message(chat_id, "Некорректная TZ. Пример: Europe/Moscow")
        else:
            tg_send_message(chat_id, "Команда не распознана. Доступно: /start, /list, /watch, /settz")

    body = json.dumps({"ok": True}).encode("utf-8")
    start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]
