from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
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
    get_result_card,
    get_state,
    get_tz,
    list_match_watches,
    mark_match_notified,
    remove_match_watch,
    set_alias,
    set_events_cache,
    set_state,
    set_tz,
    update_result_card,
)
from providers import sofascore as ss
from telegram_media import send_match_result

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")
BOT_COMMANDS = [
    {"command": "start", "description": "открыть главное меню"},
    {"command": "today", "description": "выбрать матчи на сегодня"},
    {"command": "my", "description": "показать выбранные матчи"},
    {"command": "tz", "description": "сменить часовой пояс, например Europe/Helsinki"},
]


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


def tg_set_my_commands() -> None:
    if BOT_TOKEN:
        _post_json(_tg_api("setMyCommands"), {"commands": BOT_COMMANDS})


def _tz_for(chat_id: int) -> ZoneInfo:
    try:
        return ZoneInfo(get_tz(chat_id) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _today(chat_id: int) -> dt.date:
    return dt.datetime.now(_tz_for(chat_id)).date()


def _parse_day(chat_id: int, value: Any = None) -> dt.date:
    if value:
        try:
            return dt.date.fromisoformat(str(value))
        except Exception:
            pass
    return _today(chat_id)


def _active_day(chat_id: int) -> dt.date:
    _, payload = get_state(chat_id)
    return _parse_day(chat_id, (payload or {}).get("day"))


def _day_label(chat_id: int, day: dt.date) -> str:
    today = _today(chat_id)
    if day == today:
        return "сегодня"
    if day == today - dt.timedelta(days=1):
        return "вчера"
    return day.strftime("%d.%m.%Y")


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


def _card_review_menu(card_id: str) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("все ок", f"card_ok|{card_id}"),
                _btn("исправить", f"card_fix|{card_id}"),
            ]
        ]
    )


def _card_fix_menu(card_id: str) -> Dict[str, Any]:
    return _kb(
        [
            [_btn("боковая плашка", f"card_edit|{card_id}|side")],
            [_btn("фамилии", f"card_edit|{card_id}|names")],
            [_btn("счёт", f"card_edit|{card_id}|score")],
            [_btn("назад", f"card_back|{card_id}")],
        ]
    )


def _resend_match_menu(event_id: int) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Да, выслать", f"watch_resend|{event_id}"),
                _btn("Нет", "noop"),
            ]
        ]
    )


def _card_edit_prompt(field: str) -> str:
    if field == "side":
        return "Пришли новый текст боковой плашки одним сообщением.\nПример: WTA 1000 МАДРИД   1/8 ФИНАЛА"
    if field == "names":
        return "Пришли фамилии в правильном порядке: двумя строками или через /.\nПример: Соболенко / Осака"
    return "Пришли счёт. Можно так: 2 6 6 6 / 1 7 3 2\nИли так: 2-1 (6-7, 6-3, 6-2)"


def _cut(text: str, limit: int = 92) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_names(text: str) -> Optional[tuple[str, str]]:
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    if len(lines) >= 2:
        return lines[0], lines[1]
    raw = " ".join((text or "").split())
    for sep in (" / ", "/", " — ", " - "):
        if sep in raw:
            parts = [x.strip() for x in raw.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]
    return None


def _nums(text: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", text or "")]


def _parse_score(text: str) -> Optional[tuple[List[int], List[int]]]:
    raw = " ".join((text or "").replace("\n", " / ").split())
    if "/" in raw:
        left, right = raw.split("/", 1)
        home, away = _nums(left), _nums(right)
        if len(home) >= 2 and len(away) >= 2:
            return home[:4], away[:4]

    pairs = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*[-:]\s*(\d+)", raw)]
    if not pairs:
        return None

    if len(pairs) > 1 and pairs[0][0] <= 3 and pairs[0][1] <= 3:
        total, sets = pairs[0], pairs[1:]
        if total[0] + total[1] <= len(sets):
            return [total[0]] + [a for a, _ in sets[:3]], [total[1]] + [b for _, b in sets[:3]]

    home_sets = sum(1 for a, b in pairs if a > b)
    away_sets = sum(1 for a, b in pairs if b > a)
    return [home_sets] + [a for a, _ in pairs[:3]], [away_sets] + [b for _, b in pairs[:3]]


def _apply_score(event: Dict[str, Any], home: List[int], away: List[int]) -> None:
    event["card_home_scores"] = home[:4]
    event["card_away_scores"] = away[:4]
    raw = event.setdefault("raw", {})
    home_score = raw.setdefault("homeScore", {})
    away_score = raw.setdefault("awayScore", {})
    for score in (home_score, away_score):
        for idx in range(1, 6):
            score.pop(f"period{idx}", None)
            score.pop(f"period{idx}TieBreak", None)

    home_score["current"] = home[0]
    home_score["display"] = home[0]
    away_score["current"] = away[0]
    away_score["display"] = away[0]
    for idx, value in enumerate(home[1:], start=1):
        home_score[f"period{idx}"] = value
    for idx, value in enumerate(away[1:], start=1):
        away_score[f"period{idx}"] = value
    if home[0] > away[0]:
        raw["winnerCode"] = 1
    elif away[0] > home[0]:
        raw["winnerCode"] = 2


def _handle_card_edit_text(chat_id: int, text: str, payload: Dict[str, Any]) -> None:
    card_id = str(payload.get("card_id") or "")
    field = str(payload.get("field") or "")
    event = get_result_card(chat_id, card_id)
    if not event:
        clear_state(chat_id)
        tg_send_message(chat_id, "Не нашел эту плашку для исправления. Отправь /today и выбери матч заново.")
        return

    value = (text or "").strip()
    if not value:
        tg_send_message(chat_id, _card_edit_prompt(field))
        return

    if field == "side":
        event["card_side_text"] = value.upper()
    elif field == "names":
        names = _split_names(value)
        if not names:
            tg_send_message(chat_id, "Не понял фамилии. Пришли двумя строками или через /, например: Соболенко / Осака")
            return
        home_name, away_name = names
        home_sources = [
            event.get("card_original_home_name"),
            event.get("home_name"),
            ((event.get("raw") or {}).get("homeCompetitor") or {}).get("name"),
        ]
        away_sources = [
            event.get("card_original_away_name"),
            event.get("away_name"),
            ((event.get("raw") or {}).get("awayCompetitor") or {}).get("name"),
        ]
        event["card_home_name"] = home_name
        event["card_away_name"] = away_name
        event["home_name"] = home_name
        event["away_name"] = away_name
        for sources, ru in ((home_sources, home_name), (away_sources, away_name)):
            for original in sources:
                if original and ru:
                    set_alias(str(original), str(ru))
    elif field == "score":
        scores = _parse_score(value)
        if not scores:
            tg_send_message(chat_id, "Не понял счёт. Пример: 2 6 6 6 / 1 7 3 2 или 2-1 (6-7, 6-3, 6-2)")
            return
        _apply_score(event, scores[0], scores[1])
    else:
        clear_state(chat_id)
        tg_send_message(chat_id, "Не понял, что исправлять. Нажми «исправить» под плашкой еще раз.")
        return

    update_result_card(chat_id, card_id, event)
    clear_state(chat_id)
    tg_send_message(chat_id, "Исправление принято, отправляю новую версию плашки.")
    send_match_result(BOT_TOKEN, chat_id, event)


def _load_events_for_chat(chat_id: int, day: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    day = day or _active_day(chat_id)
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


def _selected_ids(chat_id: int, day: Optional[dt.date] = None) -> set[int]:
    day = day or _active_day(chat_id)
    return {int(r["event_id"]) for r in list_match_watches(chat_id, day)}


def _find_match(chat_id: int, event_id: int, day: Optional[dt.date] = None) -> Optional[Dict[str, Any]]:
    day = day or _active_day(chat_id)
    for event in _load_events_for_chat(chat_id, day):
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


def _tournaments_map(chat_id: int, group: str, day: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    day = day or _active_day(chat_id)
    return ss.tournaments_for_tour_group(_load_events_for_chat(chat_id, day), group)


def _tournaments_title(chat_id: int, group: str, day: dt.date) -> str:
    return f"{ss.tour_label(group)} - {_day_label(chat_id, day)}\nВыбери турнир:"


def _date_switch_button(chat_id: int, group: str, day: dt.date) -> Dict[str, str]:
    today = _today(chat_id)
    if day == today:
        target = today - dt.timedelta(days=1)
        return _btn("Вчера", f"date|{group}|{target.isoformat()}")
    return _btn("Сегодня", f"date|{group}|{today.isoformat()}")


def _tournaments_menu(chat_id: int, group: str, day: Optional[dt.date] = None) -> Dict[str, Any]:
    day = day or _active_day(chat_id)
    rows: List[List[Dict[str, str]]] = []
    for idx, item in enumerate(_tournaments_map(chat_id, group, day)[:90], start=1):
        bits = [f"{item['matches_count']} матч."]
        if item.get("live_count"):
            bits.append(f"идет {item['live_count']}")
        if item.get("finished_count"):
            bits.append(f"заверш. {item['finished_count']}")
        status = str(item.get("tournament_status") or item.get("category") or "").strip()
        title = f"{status} · {item['tournament_name']}" if status else str(item["tournament_name"])
        rows.append([_btn(_cut(f"{title} ({', '.join(bits)})"), f"tour|{group}|{idx}")])
    rows.append([_date_switch_button(chat_id, group, day)])
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


def _matches_for_state(chat_id: int, group: str, tournament: str, day: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    day = day or _active_day(chat_id)
    return ss.matches_for_tournament_in_tour(_load_events_for_chat(chat_id, day), group, tournament)


def _matches_title(chat_id: int, group: str, tournament: str, day: dt.date) -> str:
    return f"{ss.tour_label(group)} - {tournament} - {_day_label(chat_id, day)}\nВыбери один или несколько матчей:"


def _matches_menu(chat_id: int, group: str, tournament: str, day: Optional[dt.date] = None) -> Dict[str, Any]:
    day = day or _active_day(chat_id)
    selected = _selected_ids(chat_id, day)
    rows: List[List[Dict[str, str]]] = []
    for match in _matches_for_state(chat_id, group, tournament, day)[:100]:
        rows.append([_btn(_match_label(chat_id, match, int(match["event_id"]) in selected), f"watch_toggle|{match['event_id']}")])
    rows.append([_btn("Готово / мои матчи", "menu|mine")])
    rows.append([_btn("К турнирам", f"back_tours|{group}")])
    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _my_matches_text(chat_id: int, day: Optional[dt.date] = None) -> str:
    day = day or _active_day(chat_id)
    rows = list_match_watches(chat_id, day)
    if not rows:
        return f"На {_day_label(chat_id, day)} пока нет выбранных матчей."

    live = {int(e["event_id"]): e for e in _load_events_for_chat(chat_id, day)}
    lines = [f"Твои матчи на {_day_label(chat_id, day)}:", ""]
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


def _my_matches_menu(chat_id: int, day: Optional[dt.date] = None) -> Dict[str, Any]:
    day = day or _active_day(chat_id)
    rows: List[List[Dict[str, str]]] = []
    for row in list_match_watches(chat_id, day)[:100]:
        time = _fmt_ts(chat_id, row.get("start_ts"))
        prefix = f"{time} - " if time else ""
        rows.append([_btn(_cut(f"Убрать: {prefix}{row['home_name']} - {row['away_name']}"), f"watch_del|{row['event_id']}")])
    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _current_choice(chat_id: int) -> tuple[Optional[str], Optional[str], dt.date]:
    _, payload = get_state(chat_id)
    payload = payload or {}
    return payload.get("group"), payload.get("tournament_name"), _parse_day(chat_id, payload.get("day"))


def _refresh_matches_message(chat_id: int, message_id: int) -> None:
    group, tournament, day = _current_choice(chat_id)
    if not group or not tournament:
        return
    tg_edit_message(
        chat_id,
        message_id,
        _matches_title(chat_id, group, tournament, day),
        reply_markup=_matches_menu(chat_id, group, tournament, day),
    )


def _handle_text(chat_id: int, text: str) -> None:
    raw = (text or "").strip()
    state, payload = get_state(chat_id)
    if state == "editing_card":
        if raw.lower() in {"/cancel", "cancel", "отмена"}:
            clear_state(chat_id)
            tg_send_message(chat_id, "Ок, исправление отменено.")
            return
        _handle_card_edit_text(chat_id, raw, payload)
        return

    cmd = raw.split(" ", 1)[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]

    if cmd in {"/start", "start", "/today", "today"}:
        tg_set_my_commands()
        clear_state(chat_id)
        tg_send_message(chat_id, "Выбери тур на сегодня:", reply_markup=_tour_groups_menu(chat_id))
        return

    if cmd in {"/my", "my"}:
        day = _active_day(chat_id)
        tg_send_message(chat_id, _my_matches_text(chat_id, day), reply_markup=_my_matches_menu(chat_id, day))
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
        if data == "noop":
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("card_ok|"):
            _, card_id = data.split("|", 1)
            tg_edit_message(chat_id, message_id, "Ок, плашка принята.")
            tg_answer_callback_query(cq_id, "Принято")
            return

        if data.startswith("card_back|"):
            _, card_id = data.split("|", 1)
            tg_edit_message(chat_id, message_id, "Плашка опубликована. Проверить?", reply_markup=_card_review_menu(card_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("card_fix|"):
            _, card_id = data.split("|", 1)
            tg_edit_message(chat_id, message_id, "Что исправить?", reply_markup=_card_fix_menu(card_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("card_edit|"):
            _, card_id, field = data.split("|", 2)
            if not get_result_card(chat_id, card_id):
                tg_answer_callback_query(cq_id, "Плашка не найдена", show_alert=True)
                return
            set_state(chat_id, "editing_card", {"card_id": card_id, "field": field})
            tg_edit_message(chat_id, message_id, _card_edit_prompt(field))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|root":
            clear_state(chat_id)
            tg_edit_message(chat_id, message_id, "Выбери тур на сегодня:", reply_markup=_tour_groups_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|mine":
            day = _active_day(chat_id)
            tg_edit_message(chat_id, message_id, _my_matches_text(chat_id, day), reply_markup=_my_matches_menu(chat_id, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("group|"):
            _, group = data.split("|", 1)
            day = _today(chat_id)
            tours = _tournaments_map(chat_id, group, day)
            if not tours:
                tg_answer_callback_query(cq_id, "На сегодня турниров не найдено", show_alert=True)
                return
            set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _tournaments_title(chat_id, group, day), reply_markup=_tournaments_menu(chat_id, group, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("date|"):
            _, group, day_s = data.split("|", 2)
            day = _parse_day(chat_id, day_s)
            tours = _tournaments_map(chat_id, group, day)
            set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
            if not tours:
                tg_answer_callback_query(cq_id, f"На {_day_label(chat_id, day)} турниров не найдено", show_alert=True)
                tg_edit_message(chat_id, message_id, _tournaments_title(chat_id, group, day), reply_markup=_tournaments_menu(chat_id, group, day))
                return
            tg_edit_message(chat_id, message_id, _tournaments_title(chat_id, group, day), reply_markup=_tournaments_menu(chat_id, group, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("back_tours|"):
            _, group = data.split("|", 1)
            day = _active_day(chat_id)
            set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _tournaments_title(chat_id, group, day), reply_markup=_tournaments_menu(chat_id, group, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("tour|"):
            _, group, idx_s = data.split("|", 2)
            idx = int(idx_s) - 1
            day = _active_day(chat_id)
            tours = _tournaments_map(chat_id, group, day)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return
            tournament = tours[idx]["tournament_name"]
            set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament, "day": day.isoformat()})
            tg_edit_message(
                chat_id,
                message_id,
                _matches_title(chat_id, group, tournament, day),
                reply_markup=_matches_menu(chat_id, group, tournament, day),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("watch_toggle|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            day = _active_day(chat_id)
            if event_id in _selected_ids(chat_id, day):
                match = _find_match(chat_id, event_id, day)
                if match and ss.is_finished(match):
                    tg_send_message(
                        chat_id,
                        "Матч уже выбран и завершен. Выслать повторно?",
                        reply_markup=_resend_match_menu(event_id),
                    )
                    tg_answer_callback_query(cq_id)
                    return
                remove_match_watch(chat_id, day, event_id)
                _refresh_matches_message(chat_id, message_id)
                tg_answer_callback_query(cq_id, "Матч убран")
                return

            match = _find_match(chat_id, event_id, day)
            if not match:
                tg_answer_callback_query(cq_id, "Матч не найден в выбранном расписании", show_alert=True)
                return

            add_match_watch(chat_id, day, match)
            if ss.is_finished(match):
                match = asyncio.run(ss.enrich_event(match))
                send_match_result(BOT_TOKEN, chat_id, match)
                mark_match_notified(chat_id, day, event_id)
                notice = "Матч уже завершен, результат отправлен отдельным сообщением"
            else:
                notice = "Матч добавлен. Результат придет отдельным сообщением после окончания."
            _refresh_matches_message(chat_id, message_id)
            tg_answer_callback_query(cq_id, notice)
            return

        if data.startswith("watch_resend|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            day = _active_day(chat_id)
            match = _find_match(chat_id, event_id, day)
            if not match:
                tg_answer_callback_query(cq_id, "Матч не найден в выбранном расписании", show_alert=True)
                return
            if not ss.is_finished(match):
                tg_answer_callback_query(cq_id, "Матч еще не завершен", show_alert=True)
                return
            match = asyncio.run(ss.enrich_event(match))
            if send_match_result(BOT_TOKEN, chat_id, match):
                mark_match_notified(chat_id, day, event_id)
                tg_answer_callback_query(cq_id, "Отправил повторно")
            else:
                tg_answer_callback_query(cq_id, "Не смог отправить", show_alert=True)
            return

        if data.startswith("watch_del|"):
            _, event_id_s = data.split("|", 1)
            day = _active_day(chat_id)
            removed = remove_match_watch(chat_id, day, int(event_id_s))
            tg_edit_message(chat_id, message_id, _my_matches_text(chat_id, day), reply_markup=_my_matches_menu(chat_id, day))
            tg_answer_callback_query(cq_id, "Матч удален" if removed else "Матч уже был удален")
            return

        tg_answer_callback_query(cq_id)
    except Exception as exc:
        print(f"[ERR] callback failed: {exc}")
        tg_answer_callback_query(cq_id, "Ошибка обработки", show_alert=True)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        tg_set_my_commands()
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
