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
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def _post_json(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = "<body read failed>"
        print(f"[tg] request failed: status={exc.code} body={body}")
    except urllib.error.URLError as exc:
        print(f"[tg] request failed: {exc}")


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
    payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
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
        return dt.datetime.fromtimestamp(int(ts), tz=_tz_for(chat_id)).strftime("%H:%M")
    except Exception:
        return ""


def _kb(rows: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": rows}


def _btn(text: str, data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": data}


def _cut(text: str, limit: int = 92) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _load_events_for_chat(chat_id: int) -> List[Dict[str, Any]]:
    day = _today(chat_id)
    data = get_events_cache(day) or {"events": []}
    events = ss.normalize_events(data)
    if events and data.get("source") == "flashscore":
        return events

    try:
        print(f"[events] fetching flashscore for {day}")
        data = asyncio.run(ss.events_by_date(day)) or {"events": []}
        set_events_cache(day, data)
        events = ss.normalize_events(data)
        print(f"[events] fetched for {day}: raw={len(data.get('events', []) or [])} normalized={len(events)}")
    except Exception as exc:
        print(f"[events] fetch failed for {day}: {exc}")
    return events


def _selected_ids(chat_id: int) -> set[int]:
    return {int(r["event_id"]) for r in list_match_watches(chat_id, _today(chat_id))}


def _find_match(chat_id: int, event_id: int) -> Optional[Dict[str, Any]]:
    for event in _load_events_for_chat(chat_id):
        if int(event["event_id"]) == int(event_id):
            return event
    return None


def _tour_groups_menu(chat_id: int) -> Dict[str, Any]:
    return _kb(
        [
            [_btn("Мужской тур", "group|men")],
            [_btn("Женский тур", "group|women")],
            [_btn("Мои матчи", "menu|mine")],
        ]
    )


def _tournaments_map(chat_id: int, group: str) -> List[Dict[str, Any]]:
    return ss.tournaments_for_tour_group(_load_events_for_chat(chat_id), group)


def _tournaments_menu(chat_id: int, group: str) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, item in enumerate(_tournaments_map(chat_id, group)[:90], start=1):
        bits = [f"{item['matches_count']} матч."]
        if item.get("live_count"):
            bits.append(f"идет {item['live_count']}")
        if item.get("finished_count"):
            bits.append(f"заверш. {item['finished_count']}")
        rows.append([_btn(_cut(f"{item['tournament_name']} ({', '.join(bits)})"), f"tour|{group}|{idx}")])
    rows.append([_btn("Назад", "menu|root")])
    return _kb(rows)


def _match_label(chat_id: int, match: Dict[str, Any], selected: bool) -> str:
    time = _fmt_ts(chat_id, match.get("start_ts"))
    status = ss.status_label(match)
    score = ss.compact_score(match)
    parts = ["[x]" if selected else "[ ]"]
    if time:
        parts.append(time)
    parts.append(status)
    if score:
        parts.append(score)
    parts.append(f"{match['home_name']} - {match['away_name']}")
    return _cut(" | ".join(parts), 100)


def _matches_for_state(chat_id: int, group: str, tournament: str) -> List[Dict[str, Any]]:
    return ss.matches_for_tournament_in_tour(_load_events_for_chat(chat_id), group, tournament)


def _matches_menu(chat_id: int, group: str, tournament: str) -> Dict[str, Any]:
    selected = _selected_ids(chat_id)
    rows: List[List[Dict[str, str]]] = []
    for match in _matches_for_state(chat_id, group, tournament)[:100]:
        rows.append([_btn(_match_label(chat_id, match, int(match["event_id"]) in selected), f"watch_toggle|{match['event_id']}")])
    rows.append([_btn("Готово / мои матчи", "menu|mine")])
    rows.append([_btn("К турнирам", f"back_tours|{group}")])
    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _my_matches_text(chat_id: int) -> str:
    rows = list_match_watches(chat_id, _today(chat_id))
    if not rows:
        return "На сегодня пока нет выбранных матчей."

    live = {int(e["event_id"]): e for e in _load_events_for_chat(chat_id)}
    lines = ["Твои матчи на сегодня:", ""]
    for row in rows:
        event = live.get(int(row["event_id"]))
        status = ss.status_label(event) if event else "Ожидает проверки"
        score = ss.compact_score(event) if event else ""
        time = _fmt_ts(chat_id, row.get("start_ts"))
        prefix = f"{time} - " if time else ""
        tail = f" | {score}" if score else ""
        lines.append(f"- {row['tournament_name']}")
        lines.append(f"  {prefix}{row['home_name']} - {row['away_name']} | {status}{tail}")
    return "\n".join(lines).strip()


def _my_matches_menu(chat_id: int) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for row in list_match_watches(chat_id, _today(chat_id))[:100]:
        time = _fmt_ts(chat_id, row.get("start_ts"))
        prefix = f"{time} - " if time else ""
        rows.append([_btn(_cut(f"Убрать: {prefix}{row['home_name']} - {row['away_name']}"), f"watch_del|{row['event_id']}")])
    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _current_choice(chat_id: int) -> tuple[Optional[str], Optional[str]]:
    _, payload = get_state(chat_id)
    payload = payload or {}
    return payload.get("group"), payload.get("tournament_name")


def _refresh_matches_message(chat_id: int, message_id: int) -> None:
    group, tournament = _current_choice(chat_id)
    if not group or not tournament:
        return
    tg_edit_message(
        chat_id,
        message_id,
        f"{ss.tour_label(group)} - {tournament}\nВыбери один или несколько матчей:",
        reply_markup=_matches_menu(chat_id, group, tournament),
    )


def _handle_text(chat_id: int, text: str) -> None:
    raw = (text or "").strip()
    cmd = raw.split(" ", 1)[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]

    if cmd in {"/start", "start", "/today", "today"}:
        tg_send_message(chat_id, "Выбери тур на сегодня:", reply_markup=_tour_groups_menu(chat_id))
        return

    if cmd in {"/my", "my"}:
        tg_send_message(chat_id, _my_matches_text(chat_id), reply_markup=_my_matches_menu(chat_id))
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

    tg_send_message(chat_id, "Команды:\n/today - выбрать матчи\n/my - мои матчи\n/tz Europe/Helsinki - сменить часовой пояс")


def _handle_callback(chat_id: int, message_id: int, cq_id: str, data: str) -> None:
    try:
        if data == "menu|root":
            clear_state(chat_id)
            tg_edit_message(chat_id, message_id, "Выбери тур на сегодня:", reply_markup=_tour_groups_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|mine":
            tg_edit_message(chat_id, message_id, _my_matches_text(chat_id), reply_markup=_my_matches_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("group|"):
            _, group = data.split("|", 1)
            tours = _tournaments_map(chat_id, group)
            if not tours:
                tg_answer_callback_query(cq_id, "На сегодня турниров не найдено", show_alert=True)
                return
            set_state(chat_id, "picked_tour_group", {"group": group})
            tg_edit_message(chat_id, message_id, f"{ss.tour_label(group)}\nВыбери турнир:", reply_markup=_tournaments_menu(chat_id, group))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("back_tours|"):
            _, group = data.split("|", 1)
            set_state(chat_id, "picked_tour_group", {"group": group})
            tg_edit_message(chat_id, message_id, f"{ss.tour_label(group)}\nВыбери турнир:", reply_markup=_tournaments_menu(chat_id, group))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("tour|"):
            _, group, idx_s = data.split("|", 2)
            idx = int(idx_s) - 1
            tours = _tournaments_map(chat_id, group)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return
            tournament = tours[idx]["tournament_name"]
            set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament})
            tg_edit_message(
                chat_id,
                message_id,
                f"{ss.tour_label(group)} - {tournament}\nВыбери один или несколько матчей:",
                reply_markup=_matches_menu(chat_id, group, tournament),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("watch_toggle|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            if event_id in _selected_ids(chat_id):
                remove_match_watch(chat_id, _today(chat_id), event_id)
                _refresh_matches_message(chat_id, message_id)
                tg_answer_callback_query(cq_id, "Матч убран")
                return

            match = _find_match(chat_id, event_id)
            if not match:
                tg_answer_callback_query(cq_id, "Матч не найден в сегодняшнем расписании", show_alert=True)
                return

            add_match_watch(chat_id, _today(chat_id), match)
            if ss.is_finished(match):
                match = asyncio.run(ss.enrich_event(match))
                tg_send_message(chat_id, ss.result_message(match))
                mark_match_notified(chat_id, _today(chat_id), event_id)
                notice = "Матч уже завершен, результат отправлен отдельным сообщением"
            else:
                notice = "Матч добавлен. Результат придет отдельным сообщением после окончания."
            _refresh_matches_message(chat_id, message_id)
            tg_answer_callback_query(cq_id, notice)
            return

        if data.startswith("watch_del|"):
            _, event_id_s = data.split("|", 1)
            removed = remove_match_watch(chat_id, _today(chat_id), int(event_id_s))
            tg_edit_message(chat_id, message_id, _my_matches_text(chat_id), reply_markup=_my_matches_menu(chat_id))
            tg_answer_callback_query(cq_id, "Матч удален" if removed else "Матч уже был удален")
            return

        tg_answer_callback_query(cq_id)
    except Exception as exc:
        print(f"[ERR] callback failed: {exc}")
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
                chat_id = int((msg.get("chat") or {})["id"])
                _handle_text(chat_id, msg.get("text") or "")
            elif "callback_query" in upd:
                cq = upd["callback_query"] or {}
                msg = cq.get("message") or {}
                chat_id = int((msg.get("chat") or {})["id"])
                _handle_callback(chat_id, int(msg["message_id"]), cq.get("id") or "", cq.get("data") or "")
            else:
                print(f"[webhook] unsupported update keys={list(upd.keys())}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as exc:
            print(f"[ERR] webhook fatal: {exc}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
