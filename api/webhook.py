from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from db_pg import (
    add_match_watch,
    clear_state,
    ensure_schema,
    get_events_cache,
    get_state,
    get_tz,
    list_match_watches,
    remove_match_watch,
    set_state,
    set_tz,
)
from providers import sofascore as ss

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Tallinn")


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def _post_json(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError as e:
        print(f"[tg] request failed: {e}")


def tg_send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    if not BOT_TOKEN:
        return
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post_json(_tg_api("sendMessage"), payload)


def tg_edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
    if not BOT_TOKEN:
        return
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _post_json(_tg_api("editMessageText"), payload)


def tg_answer_callback_query(cb_id: str, text: Optional[str] = None, show_alert: bool = False) -> None:
    if not BOT_TOKEN:
        return
    payload: Dict[str, Any] = {"callback_query_id": cb_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    _post_json(_tg_api("answerCallbackQuery"), payload)


def _tz_for(chat_id: int) -> ZoneInfo:
    try:
        return ZoneInfo(get_tz(chat_id) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _today(chat_id: int) -> dt.date:
    return dt.datetime.now(_tz_for(chat_id)).date()


def _fmt_ts(chat_id: int, ts: Optional[int]) -> str:
    if not ts:
        return ""
    try:
        d = dt.datetime.fromtimestamp(ts, tz=_tz_for(chat_id))
        return d.strftime("%H:%M")
    except Exception:
        return ""


def _kb(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": rows}


def _btn(text: str, data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": data}


def _chunk_buttons(items: List[Dict[str, str]], width: int = 1) -> List[List[Dict[str, str]]]:
    out: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for item in items:
        row.append(item)
        if len(row) >= width:
            out.append(row)
            row = []
    if row:
        out.append(row)
    return out


def _load_events_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    data = get_events_cache(_today(chat_id)) or {"events": []}
    return ss.normalize_events(data)


def _categories_menu(chat_id: int) -> Dict[str, Any]:
    rows = [
        [_btn("ATP", "cat|ATP")],
        [_btn("WTA", "cat|WTA")],
        [_btn("ITF", "cat|ITF")],
        [_btn("Мои матчи", "menu|mine")],
    ]
    return _kb(rows)


def _tournaments_menu(chat_id: int, category: str) -> Dict[str, Any]:
    events = _load_events_for_chat(chat_id)
    tours = ss.tournaments_for_category(events, category)
    rows: List[List[Dict[str, str]]] = []

    for i, t in enumerate(tours[:80], start=1):
        label = f"{t['tournament_name']} ({t['matches_count']})"
        rows.append([_btn(label, f"tour|{category}|{i}")])

    rows.append([_btn("← Назад", "menu|root")])
    return _kb(rows)


def _tournaments_map(chat_id: int, category: str) -> List[Dict[str, Any]]:
    events = _load_events_for_chat(chat_id)
    return ss.tournaments_for_category(events, category)


def _matches_menu(chat_id: int, category: str, tournament_name: str) -> Dict[str, Any]:
    events = _load_events_for_chat(chat_id)
    matches = ss.matches_for_tournament(events, category, tournament_name)

    rows: List[List[Dict[str, str]]] = []
    for m in matches[:100]:
        tm = _fmt_ts(chat_id, m.get("start_ts"))
        prefix = f"{tm} · " if tm else ""
        label = f"{prefix}{m['home_name']} — {m['away_name']}"
        rows.append([_btn(label, f"watch_add|{m['event_id']}")])

    rows.append([_btn("← К турнирам", f"back_tours|{category}")])
    rows.append([_btn("⌂ В начало", "menu|root")])
    return _kb(rows)


def _find_match_by_id(chat_id: int, event_id: int) -> Optional[Dict[str, Any]]:
    events = _load_events_for_chat(chat_id)
    for e in events:
        if int(e["event_id"]) == int(event_id):
            return e
    return None


def _my_matches_text(chat_id: int) -> str:
    rows = list_match_watches(chat_id, _today(chat_id))
    if not rows:
        return "На сегодня у тебя пока нет выбранных матчей."

    lines = ["Твои матчи на сегодня:", ""]
    for r in rows:
        tm = _fmt_ts(chat_id, r.get("start_ts"))
        prefix = f"{tm} · " if tm else ""
        lines.append(f"• [{r['category']}] {r['tournament_name']}")
        lines.append(f"  {prefix}{r['home_name']} — {r['away_name']}")
        lines.append("")
    return "\n".join(lines).strip()


def _my_matches_menu(chat_id: int) -> Dict[str, Any]:
    rows_db = list_match_watches(chat_id, _today(chat_id))
    rows: List[List[Dict[str, str]]] = []

    for r in rows_db[:100]:
        tm = _fmt_ts(chat_id, r.get("start_ts"))
        prefix = f"{tm} · " if tm else ""
        rows.append([_btn(f"❌ {prefix}{r['home_name']} — {r['away_name']}", f"watch_del|{r['event_id']}")])

    rows.append([_btn("⌂ В начало", "menu|root")])
    return _kb(rows)


def _handle_text(chat_id: int, text: str) -> None:
    raw = (text or "").strip()

    if raw == "/start":
        tg_send_message(
            chat_id,
            "Привет! Выбери категорию тура на сегодня:",
            reply_markup=_categories_menu(chat_id),
        )
        return

    if raw == "/today":
        tg_send_message(
            chat_id,
            "Выбери категорию тура на сегодня:",
            reply_markup=_categories_menu(chat_id),
        )
        return

    if raw == "/my":
        tg_send_message(
            chat_id,
            _my_matches_text(chat_id),
            reply_markup=_my_matches_menu(chat_id),
        )
        return

    if raw.startswith("/tz "):
        tz = raw.split(" ", 1)[1].strip()
        try:
            ZoneInfo(tz)
            set_tz(chat_id, tz)
            tg_send_message(chat_id, f"Ок, timezone сохранен: {tz}")
        except Exception:
            tg_send_message(chat_id, "Не смог распознать timezone. Пример: Europe/Tallinn")
        return

    tg_send_message(
        chat_id,
        "Команды:\n"
        "/today — выбрать матчи на сегодня\n"
        "/my — мои выбранные матчи\n"
        "/tz Europe/Tallinn — сменить часовой пояс",
    )


def _handle_callback(chat_id: int, message_id: int, cq_id: str, data: str) -> None:
    try:
        if data == "menu|root":
            clear_state(chat_id)
            tg_edit_message(
                chat_id,
                message_id,
                "Выбери категорию тура на сегодня:",
                reply_markup=_categories_menu(chat_id),
            )
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|mine":
            tg_edit_message(
                chat_id,
                message_id,
                _my_matches_text(chat_id),
                reply_markup=_my_matches_menu(chat_id),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("cat|"):
            _, category = data.split("|", 1)
            tours = _tournaments_map(chat_id, category)
            if not tours:
                tg_answer_callback_query(cq_id, "На сегодня турниров не найдено", show_alert=True)
                return

            set_state(chat_id, "picked_category", {"category": category})
            tg_edit_message(
                chat_id,
                message_id,
                f"Категория: {category}\nВыбери турнир:",
                reply_markup=_tournaments_menu(chat_id, category),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("back_tours|"):
            _, category = data.split("|", 1)
            tg_edit_message(
                chat_id,
                message_id,
                f"Категория: {category}\nВыбери турнир:",
                reply_markup=_tournaments_menu(chat_id, category),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("tour|"):
            _, category, idx_s = data.split("|", 2)
            idx = int(idx_s) - 1
            tours = _tournaments_map(chat_id, category)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return

            tournament_name = tours[idx]["tournament_name"]
            set_state(chat_id, "picked_tournament", {"category": category, "tournament_name": tournament_name})

            tg_edit_message(
                chat_id,
                message_id,
                f"{category} → {tournament_name}\nВыбери матч:",
                reply_markup=_matches_menu(chat_id, category, tournament_name),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("watch_add|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            row = _find_match_by_id(chat_id, event_id)
            if not row:
                tg_answer_callback_query(cq_id, "Матч не найден в сегодняшнем расписании", show_alert=True)
                return

            added = add_match_watch(chat_id, _today(chat_id), row)
            if added:
                tg_answer_callback_query(cq_id, "Матч добавлен")
            else:
                tg_answer_callback_query(cq_id, "Этот матч уже добавлен")
            return

        if data.startswith("watch_del|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            removed = remove_match_watch(chat_id, _today(chat_id), event_id)
            tg_edit_message(
                chat_id,
                message_id,
                _my_matches_text(chat_id),
                reply_markup=_my_matches_menu(chat_id),
            )
            tg_answer_callback_query(cq_id, "Матч удален" if removed else "Матч уже был удален")
            return

        tg_answer_callback_query(cq_id)
    except Exception as e:
        print(f"[ERR] callback failed: {e}")
        tg_answer_callback_query(cq_id, "Ошибка обработки", show_alert=True)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "service": "tennis-webhook"}).encode("utf-8"))

    def do_POST(self):
        try:
            if WEBHOOK_SECRET:
                got = self.headers.get("x-telegram-bot-api-secret-token", "")
                if got != WEBHOOK_SECRET:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"forbidden"}')
                    return

            ensure_schema()

            length = int(self.headers.get("content-length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            upd = json.loads(raw.decode("utf-8"))

            if "message" in upd:
                msg = upd["message"] or {}
                chat = msg.get("chat") or {}
                chat_id = int(chat["id"])
                text = msg.get("text") or ""
                _handle_text(chat_id, text)

            elif "callback_query" in upd:
                cq = upd["callback_query"] or {}
                cq_id = cq.get("id") or ""
                data = cq.get("data") or ""
                msg = cq.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = int(chat["id"])
                message_id = int(msg["message_id"])
                _handle_callback(chat_id, message_id, cq_id, data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            print(f"[ERR] webhook fatal: {e}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
