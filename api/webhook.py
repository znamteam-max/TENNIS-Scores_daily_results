from __future__ import annotations

import asyncio
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
    mark_match_notified,
    remove_match_watch,
    set_events_cache,
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
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = "<failed to read response body>"
        safe_payload = dict(payload)
        if "reply_markup" in safe_payload:
            safe_payload["reply_markup"] = "<present>"
        print(f"[tg] request failed: status={e.code} body={body} payload={safe_payload}")
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
    day = _today(chat_id)
    data = get_events_cache(day) or {"events": []}
    events = ss.normalize_events(data)
    if events:
        return events

    try:
        print(f"[events] cache empty for {day}; fetching sofascore")
        data = asyncio.run(ss.events_by_date(day)) or {"events": []}
        set_events_cache(day, data)
        events = ss.normalize_events(data)
        print(f"[events] fetched for {day}: raw={len(data.get('events', []) or [])} normalized={len(events)}")
    except Exception as e:
        print(f"[events] fallback fetch failed for {day}: {e}")

    return events


def _tour_groups_menu(chat_id: int) -> Dict[str, Any]:
    rows = [
        [_btn("Мужской тур", "group|men")],
        [_btn("Женский тур", "group|women")],
        [_btn("Мои матчи", "menu|mine")],
    ]
    return _kb(rows)


def _tournaments_menu(chat_id: int, group: str) -> Dict[str, Any]:
    events = _load_events_for_chat(chat_id)
    tours = ss.tournaments_for_tour_group(events, group)
    rows: List[List[Dict[str, str]]] = []

    for i, t in enumerate(tours[:80], start=1):
        label = f"{t['tournament_name']} ({t['matches_count']})"
        rows.append([_btn(label, f"tour|{group}|{i}")])

    rows.append([_btn("Назад", "menu|root")])
    return _kb(rows)


def _tournaments_map(chat_id: int, group: str) -> List[Dict[str, Any]]:
    events = _load_events_for_chat(chat_id)
    return ss.tournaments_for_tour_group(events, group)


def _matches_menu(chat_id: int, group: str, tournament_name: str) -> Dict[str, Any]:
    events = _load_events_for_chat(chat_id)
    matches = ss.matches_for_tournament_in_tour(events, group, tournament_name)

    rows: List[List[Dict[str, str]]] = []
    for m in matches[:100]:
        tm = _fmt_ts(chat_id, m.get("start_ts"))
        prefix = f"{tm} - " if tm else ""
        label = f"{prefix}{m['home_name']} - {m['away_name']}"
        rows.append([_btn(label, f"watch_add|{m['event_id']}")])

    rows.append([_btn("К турнирам", f"back_tours|{group}")])
    rows.append([_btn("В начало", "menu|root")])
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
        prefix = f"{tm} - " if tm else ""
        lines.append(f"- [{r['category']}] {r['tournament_name']}")
        lines.append(f"  {prefix}{r['home_name']} - {r['away_name']}")
        lines.append("")
    return "\n".join(lines).strip()


def _my_matches_menu(chat_id: int) -> Dict[str, Any]:
    rows_db = list_match_watches(chat_id, _today(chat_id))
    rows: List[List[Dict[str, str]]] = []

    for r in rows_db[:100]:
        tm = _fmt_ts(chat_id, r.get("start_ts"))
        prefix = f"{tm} - " if tm else ""
        rows.append([_btn(f"Удалить: {prefix}{r['home_name']} - {r['away_name']}", f"watch_del|{r['event_id']}")])

    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _handle_text(chat_id: int, text: str) -> None:
    raw = (text or "").strip()
    cmd = raw.split(" ", 1)[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]

    if cmd in {"/start", "start"}:
        tg_send_message(
            chat_id,
            "Привет! Выбери тур на сегодня:",
            reply_markup=_tour_groups_menu(chat_id),
        )
        return

    if cmd in {"/today", "today"}:
        tg_send_message(
            chat_id,
            "Выбери тур на сегодня:",
            reply_markup=_tour_groups_menu(chat_id),
        )
        return

    if cmd in {"/my", "my"}:
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
            tg_send_message(chat_id, "Не смог распознать timezone. Пример: Europe/Helsinki")
        return

    tg_send_message(
        chat_id,
        "Команды:\n"
        "/today - выбрать матчи на сегодня\n"
        "/my - мои выбранные матчи\n"
        "/tz Europe/Helsinki - сменить часовой пояс",
    )


def _handle_callback(chat_id: int, message_id: int, cq_id: str, data: str) -> None:
    try:
        if data == "menu|root":
            clear_state(chat_id)
            tg_edit_message(
                chat_id,
                message_id,
                "Выбери тур на сегодня:",
                reply_markup=_tour_groups_menu(chat_id),
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

        if data.startswith("group|"):
            _, group = data.split("|", 1)
            tours = _tournaments_map(chat_id, group)
            if not tours:
                tg_answer_callback_query(cq_id, "На сегодня турниров не найдено", show_alert=True)
                return

            set_state(chat_id, "picked_tour_group", {"group": group})
            tg_edit_message(
                chat_id,
                message_id,
                f"{ss.tour_label(group)}\nВыбери турнир:",
                reply_markup=_tournaments_menu(chat_id, group),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("back_tours|"):
            _, group = data.split("|", 1)
            tg_edit_message(
                chat_id,
                message_id,
                f"{ss.tour_label(group)}\nВыбери турнир:",
                reply_markup=_tournaments_menu(chat_id, group),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("tour|"):
            _, group, idx_s = data.split("|", 2)
            idx = int(idx_s) - 1
            tours = _tournaments_map(chat_id, group)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return

            tournament_name = tours[idx]["tournament_name"]
            set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament_name})

            tg_edit_message(
                chat_id,
                message_id,
                f"{ss.tour_label(group)} - {tournament_name}\nВыбери матч:",
                reply_markup=_matches_menu(chat_id, group, tournament_name),
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
            if added and ss.is_finished(row):
                tg_send_message(chat_id, ss.result_message(row))
                mark_match_notified(chat_id, _today(chat_id), event_id)
                tg_answer_callback_query(cq_id, "Матч уже завершен, результат отправлен")
            elif added:
                tg_answer_callback_query(cq_id, "Матч добавлен. Результат придет после окончания.")
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
                print(f"[webhook] message chat_id={chat_id} text={text!r}")
                _handle_text(chat_id, text)

            elif "callback_query" in upd:
                cq = upd["callback_query"] or {}
                cq_id = cq.get("id") or ""
                data = cq.get("data") or ""
                msg = cq.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = int(chat["id"])
                message_id = int(msg["message_id"])
                print(f"[webhook] callback chat_id={chat_id} data={data!r}")
                _handle_callback(chat_id, message_id, cq_id, data)
            else:
                print(f"[webhook] unsupported update keys={list(upd.keys())}")

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
