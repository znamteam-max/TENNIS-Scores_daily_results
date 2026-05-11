from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
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
    get_summary_review,
    get_events_cache,
    get_result_card,
    get_state,
    get_tz,
    list_match_watches,
    mark_match_notified,
    remove_match_watch,
    ru_name_for,
    save_summary_review,
    set_alias,
    set_events_cache,
    set_summary_review_message,
    set_state,
    set_tz,
    update_summary_review_overrides,
    update_result_card,
)
from daily_summary import (
    build_daily_summary_for_tournament,
    mark_daily_summary_for_tournament,
    summary_events_for_tournament,
    summary_tournaments_for_menu,
)
from providers import sofascore as ss
from telegram_media import send_match_result

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")
PUBLISH_CHAT_ID = (
    os.getenv("PUBLISH_CHAT_ID")
    or os.getenv("RESULTS_CHAT_ID")
    or os.getenv("TELEGRAM_PUBLISH_CHAT_ID")
    or ""
).strip()
BOT_COMMANDS = [
    {"command": "start", "description": "открыть главное меню"},
    {"command": "today", "description": "выбрать матчи на сегодня"},
    {"command": "summary", "description": "опубликовать итоги игрового дня"},
    {"command": "my", "description": "показать выбранные матчи"},
    {"command": "tz", "description": "сменить часовой пояс, например Europe/Helsinki"},
]
_ALIAS_CACHE: Dict[str, str] = {}


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def _publish_chat_id(chat_id: int) -> int | str:
    return PUBLISH_CHAT_ID or chat_id


def _send_result(chat_id: int, event: Dict[str, Any]) -> bool:
    return send_match_result(
        BOT_TOKEN,
        _publish_chat_id(chat_id),
        event,
        review_chat_id=chat_id,
        review_in_publish_chat=bool(PUBLISH_CHAT_ID),
        allow_text_fallback=False,
    )


def _request_json(url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body else {"ok": True}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = "<body read failed>"
        print(f"[tg] request failed: status={exc.code} body={body}")
        return None
    except urllib.error.URLError as exc:
        print(f"[tg] request failed: {exc}")
        return None


def _post_json(url: str, payload: Dict[str, Any]) -> bool:
    data = _request_json(url, payload)
    return bool(data and data.get("ok", True))


def tg_send_message(chat_id: int | str, text: str, reply_markup: Optional[dict] = None) -> bool:
    return bool(tg_send_message_result(chat_id, text, reply_markup=reply_markup))


def tg_send_message_result(chat_id: int | str, text: str, reply_markup: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    if not BOT_TOKEN:
        return None
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _request_json(_tg_api("sendMessage"), payload)


def tg_send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    if BOT_TOKEN:
        _post_json(_tg_api("sendChatAction"), {"chat_id": chat_id, "action": action})


def tg_edit_message(chat_id: int | str, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
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
    if day == today + dt.timedelta(days=1):
        return "завтра"
    return day.strftime("%d.%m.%Y")


def _day_label_full(chat_id: int, day: dt.date) -> str:
    return f"{_day_label(chat_id, day)} ({day.isoformat()})"


def _relative_day(chat_id: int, value: str) -> dt.date:
    today = _today(chat_id)
    if value == "yesterday":
        return today - dt.timedelta(days=1)
    return today


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
            [_btn("фото", f"card_edit|{card_id}|photo")],
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
    if field == "photo":
        return "Пришли фото картинкой, файлом или прямой ссылкой на изображение. Чтобы убрать фото, напиши: убрать"
    return "Пришли счёт. Можно так: 2 6 6 6 / 1 7 3 2\nИли так: 2-1 (6-7, 6-3, 6-2)"


def _group_edit_prompt(field: str) -> str:
    return _card_edit_prompt(field) + "\n\nВ группе ответь реплаем на это сообщение, чтобы бот точно увидел правку."


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
                    _set_alias_forever(str(original), str(ru))
    elif field == "score":
        scores = _parse_score(value)
        if not scores:
            tg_send_message(chat_id, "Не понял счёт. Пример: 2 6 6 6 / 1 7 3 2 или 2-1 (6-7, 6-3, 6-2)")
            return
        _apply_score(event, scores[0], scores[1])
    elif field == "photo":
        low = value.lower()
        if low in {"убрать", "remove", "delete", "без фото"}:
            event.pop("card_photo_url", None)
            event.pop("card_photo_file_id", None)
        elif value.startswith(("http://", "https://")):
            event["card_photo_url"] = value
            event.pop("card_photo_file_id", None)
        else:
            tg_send_message(chat_id, "Пришли фото картинкой, файлом или прямой ссылкой на изображение.")
            return
    else:
        clear_state(chat_id)
        tg_send_message(chat_id, "Не понял, что исправлять. Нажми «исправить» под плашкой еще раз.")
        return

    update_result_card(chat_id, card_id, event)
    clear_state(chat_id)
    tg_send_message(chat_id, "Исправление принято, отправляю новую версию плашки.")
    _send_result(chat_id, event)


def _message_photo_file_id(msg: Dict[str, Any]) -> str:
    photos = msg.get("photo") or []
    if photos:
        return str(photos[-1].get("file_id") or "")
    doc = msg.get("document") or {}
    mime = str(doc.get("mime_type") or "")
    if mime.startswith("image/"):
        return str(doc.get("file_id") or "")
    return ""


def _handle_card_photo_upload(chat_id: int, msg: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    file_id = _message_photo_file_id(msg)
    if not file_id:
        return False
    card_id = str(payload.get("card_id") or "")
    event = get_result_card(chat_id, card_id)
    if not event:
        clear_state(chat_id)
        tg_send_message(chat_id, "Не нашел эту плашку для исправления. Отправь /today и выбери матч заново.")
        return True
    event["card_photo_file_id"] = file_id
    event.pop("card_photo_url", None)
    update_result_card(chat_id, card_id, event)
    clear_state(chat_id)
    tg_send_message(chat_id, "Фото принято, отправляю новую версию плашки.")
    _send_result(chat_id, event)
    return True


def _load_events_for_chat(chat_id: int, day: Optional[dt.date] = None, *, force_refresh: bool = False) -> List[Dict[str, Any]]:
    day = day or _active_day(chat_id)
    events: List[Dict[str, Any]] = []
    if not force_refresh:
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
        data = get_events_cache(day) or {"events": []}
        events = ss.normalize_events(data)
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
            [_btn("📅 Расписание", "menu|schedule")],
            [
                _btn("Мужской тур сегодня", "group|men"),
                _btn("Женский тур сегодня", "group|women"),
            ],
            [_btn("📊 Итоги дня", "menu|summary")],
            [_btn("Мои матчи", "menu|mine")],
        ]
    )


def _schedule_dates_menu(chat_id: int) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Сегодня", "sched_date_rel|today"),
                _btn("Вчера", "sched_date_rel|yesterday"),
            ],
            [_btn("Назад", "menu|root")],
        ]
    )


def _schedule_groups_title(chat_id: int, day: dt.date) -> str:
    return f"📅 Расписание - {_day_label_full(chat_id, day)}\nВыбери тур:"


def _schedule_groups_menu(day: dt.date) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Мужчины", f"sched_group|men|{day.isoformat()}"),
                _btn("Женщины", f"sched_group|women|{day.isoformat()}"),
            ],
            [_btn("К датам", "menu|schedule")],
            [_btn("В начало", "menu|root")],
        ]
    )


def _tournaments_map(chat_id: int, group: str, day: Optional[dt.date] = None) -> List[Dict[str, Any]]:
    day = day or _active_day(chat_id)
    return ss.tournaments_for_tour_group(_load_events_for_chat(chat_id, day), group)


def _tournaments_title(chat_id: int, group: str, day: dt.date) -> str:
    return f"{ss.tour_label(group)} - {_day_label_full(chat_id, day)}\nВыбери турнир:"


def _date_nav_buttons(chat_id: int, group: str, day: dt.date) -> List[Dict[str, str]]:
    today = _today(chat_id)
    prev_day = day - dt.timedelta(days=1)
    today_button = _btn("Сегодня", f"date|{group}|{today.isoformat()}")
    if day == today:
        today_button = _btn("Сегодня ✓", "noop")
    return [
        _btn("← День назад", f"date|{group}|{prev_day.isoformat()}"),
        today_button,
    ]


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
    rows.append(_date_nav_buttons(chat_id, group, day))
    rows.append([_btn("Назад", "menu|root")])
    return _kb(rows)


def _summary_dates_menu(chat_id: int) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Сегодня", "sum_date_rel|today"),
                _btn("Вчера", "sum_date_rel|yesterday"),
            ],
            [_btn("Назад", "menu|root")],
        ]
    )


def _summary_groups_title(chat_id: int, day: dt.date) -> str:
    return f"📊 Итоги игрового дня - {_day_label_full(chat_id, day)}\nВыбери тур:"


def _summary_groups_menu(chat_id: int, day: dt.date) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Мужчины", f"sum_group|men|{day.isoformat()}"),
                _btn("Женщины", f"sum_group|women|{day.isoformat()}"),
            ],
            [_btn("К датам", "menu|summary")],
            [_btn("В начало", "menu|root")],
        ]
    )


def _summary_tournaments_map(chat_id: int, group: str, day: dt.date) -> List[Dict[str, Any]]:
    return [
        item
        for item in summary_tournaments_for_menu(_load_events_for_chat(chat_id, day, force_refresh=True))
        if item.get("tour_group") == group
    ]


def _summary_tournaments_title(chat_id: int, group: str, day: dt.date) -> str:
    return f"📊 {_day_label_full(chat_id, day).capitalize()} - {ss.tour_label(group).lower()}\nВыбери турнир:"


def _summary_tournament_label(item: Dict[str, Any]) -> str:
    status = str(item.get("tournament_status") or "").strip()
    name = str(item.get("tournament_name") or "").strip()
    title = f"{status} · {name}" if status else name
    bits = [f"{item.get('finished_count', 0)}/{item.get('matches_count', 0)} заверш."]
    if item.get("live_count"):
        bits.append(f"идет {item['live_count']}")
    bits.append("коэф." if item.get("has_odds") else "без коэф.")
    return _cut(f"{title} ({', '.join(bits)})", 100)


def _summary_tournaments_menu(chat_id: int, group: str, day: dt.date) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, item in enumerate(_summary_tournaments_map(chat_id, group, day)[:90], start=1):
        rows.append([_btn(_summary_tournament_label(item), f"sum_tour|{group}|{day.isoformat()}|{idx}")])
    rows.append([_btn("К турам", f"sum_date|{day.isoformat()}")])
    rows.append([_btn("В начало", "menu|root")])
    return _kb(rows)


def _summary_tournament_title(chat_id: int, group: str, day: dt.date, item: Dict[str, Any]) -> str:
    status = str(item.get("tournament_status") or "").strip()
    name = str(item.get("tournament_name") or "").strip()
    title = f"{status} · {name}" if status else name
    state = "все матчи завершены" if item.get("all_finished") else "день еще не завершен"
    odds = "коэффициенты есть" if item.get("has_odds") else "коэффициентов нет"
    return (
        f"📊 {title}\n"
        f"{ss.tour_label(group)} - {_day_label_full(chat_id, day)}\n"
        f"{item.get('finished_count', 0)}/{item.get('matches_count', 0)} завершено, {state}, {odds}."
    )


def _summary_tournament_menu(group: str, day: dt.date, idx: int) -> Dict[str, Any]:
    return _kb(
        [
            [_btn("Опубликовать итог дня", f"sum_publish|{group}|{day.isoformat()}|{idx}")],
            [_btn("К турнирам", f"sum_group|{group}|{day.isoformat()}")],
            [_btn("В начало", "menu|root")],
        ]
    )


def _summary_publish_confirm_menu(group: str, day: dt.date, idx: int) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Да, опубликовать", f"sum_publish_force|{group}|{day.isoformat()}|{idx}"),
                _btn("Нет", f"sum_tour|{group}|{day.isoformat()}|{idx}"),
            ],
            [_btn("К турнирам", f"sum_group|{group}|{day.isoformat()}")],
        ]
    )


SUMMARY_CATEGORY_LABELS = {
    "unexpected": "⚡ Сенсации",
    "surprise": "⚡ Неожиданно",
    "expected": "👌🏻 Ожидаемо",
    "pickem": "🟰 50/50",
    "sad": "😥 Грустно",
    "no_odds": "Без коэффициентов",
}


def _summary_id(day: dt.date, group: str, tournament: str, status: str) -> str:
    raw = "|".join([day.isoformat(), group or "", tournament or "", status or "", os.urandom(4).hex()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _summary_review_menu(summary_id: str) -> Dict[str, Any]:
    return _kb(
        [
            [_btn("✏️ Редактировать итог", f"sum_edit|{summary_id}")],
        ]
    )


def _summary_approval_menu(summary_id: str) -> Dict[str, Any]:
    return _kb(
        [
            [
                _btn("Опубликовать", f"auto_sum_publish|{summary_id}"),
                _btn("Не публиковать", f"auto_sum_skip|{summary_id}"),
            ]
        ]
    )


def _summary_edit_menu(summary_id: str) -> Dict[str, Any]:
    return _kb(
        [
            [_btn("Фамилии", f"sum_names_menu|{summary_id}")],
            [_btn("Перенести матч в раздел", f"sum_move_menu|{summary_id}")],
            [_btn("Назад к итогу", f"sum_back|{summary_id}")],
        ]
    )


def _summary_event_label(event: Dict[str, Any]) -> str:
    score = ss.compact_score(event)
    tail = f" | {score}" if score else ""
    return _cut(f"{_display_side_name(event.get('home_name'))} - {_display_side_name(event.get('away_name'))}{tail}", 88)


def _summary_events_menu(summary_id: str, action: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for idx, event in enumerate(events[:80], start=1):
        rows.append([_btn(_summary_event_label(event), f"{action}|{summary_id}|{idx}")])
    rows.append([_btn("Назад", f"sum_edit|{summary_id}")])
    return _kb(rows)


def _summary_category_menu(summary_id: str, idx: int) -> Dict[str, Any]:
    rows = [[_btn(label, f"sum_setcat|{summary_id}|{idx}|{key}")] for key, label in SUMMARY_CATEGORY_LABELS.items()]
    rows.append([_btn("Назад", f"sum_move_menu|{summary_id}")])
    return _kb(rows)


def _summary_review_text(review: Dict[str, Any]) -> str:
    text, _status, _stage = build_daily_summary_for_tournament(
        review["day"],
        review.get("events") or [],
        str(review.get("tour_group") or ""),
        str(review.get("tournament_name") or ""),
        str(review.get("tournament_status") or ""),
        overrides=review.get("overrides") or {},
    )
    return text


def _refresh_summary_review(summary_id: str, reply_markup: Optional[dict] = None) -> bool:
    review = get_summary_review(summary_id)
    if not review or not review.get("message_id"):
        return False
    text = _summary_review_text(review)
    if not text:
        return False
    tg_edit_message(str(review["chat_id"]), int(review["message_id"]), text, reply_markup=reply_markup or _summary_review_menu(summary_id))
    return True


def _summary_event_by_index(review: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    events = review.get("events") or []
    pos = idx - 1
    if pos < 0 or pos >= len(events):
        return None
    return events[pos]


def _alias_or_original(name: Any) -> str:
    raw = " ".join(str(name or "").split())
    if not raw:
        return raw
    if raw in _ALIAS_CACHE:
        return _ALIAS_CACHE[raw] or raw
    try:
        alias, ok = ru_name_for(raw)
        if ok and alias:
            _ALIAS_CACHE[raw] = alias
            return alias
    except Exception as exc:
        print(f"[names] alias lookup failed: {exc}")
    _ALIAS_CACHE[raw] = ""
    return raw


def _display_side_name(name: Any) -> str:
    return _alias_or_original(name) or "TBD"


def _set_alias_forever(original: str, ru_name: str) -> None:
    set_alias(original, ru_name)
    raw = " ".join(str(original or "").split())
    if raw:
        _ALIAS_CACHE[raw] = " ".join(str(ru_name or "").split())


def _side_alias_sources(event: Dict[str, Any], side: str) -> List[str]:
    sources: List[str] = [str(event.get(f"{side}_name") or "").strip()]
    raw = event.get("raw") or {}
    keys = ["homePlayer", "homeCompetitor", "homeTeam", "home"] if side == "home" else ["awayPlayer", "awayCompetitor", "awayTeam", "away"]
    for key in keys:
        obj = raw.get(key)
        if isinstance(obj, dict):
            sources.extend([str(obj.get("name") or "").strip(), str(obj.get("shortName") or "").strip()])
    out: List[str] = []
    seen: set[str] = set()
    for source in sources:
        if not source or source.upper() == "TBD" or source in seen:
            continue
        seen.add(source)
        out.append(source)
    return out


def _save_side_aliases(event: Dict[str, Any], side: str, ru_name: str) -> None:
    for original in _side_alias_sources(event, side):
        _set_alias_forever(original, ru_name)


def _match_names_edit_prompt(chat_id: int, match: Dict[str, Any]) -> str:
    home = _display_side_name(match.get("home_name"))
    away = _display_side_name(match.get("away_name"))
    return (
        "Пришли правильные фамилии в этом же порядке: двумя строками или через /.\n"
        f"Сейчас: {home} / {away}\n"
        "Пример: Соболенко / Осака\n\n"
        "Я сохраню исправление в базу и буду использовать его дальше. Отмена: /cancel"
    )


def _handle_match_names_edit(chat_id: int, text: str, payload: Dict[str, Any]) -> None:
    names = _split_names(text)
    if not names:
        tg_send_message(chat_id, "Не понял фамилии. Пришли двумя строками или через /, например: Соболенко / Осака")
        return

    day = _parse_day(chat_id, payload.get("day"))
    event_id = int(payload.get("event_id") or 0)
    match = _find_match(chat_id, event_id, day)
    if not match:
        clear_state(chat_id)
        tg_send_message(chat_id, "Не нашел матч в выбранном расписании. Открой /today и выбери его заново.")
        return

    home_name, away_name = names
    _save_side_aliases(match, "home", home_name)
    _save_side_aliases(match, "away", away_name)

    group = str(payload.get("group") or "")
    tournament = str(payload.get("tournament_name") or "")
    if group and tournament:
        set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament, "day": day.isoformat()})
        tg_send_message(
            chat_id,
            f"Сохранил навсегда: {home_name} / {away_name}",
            reply_markup=_matches_menu(chat_id, group, tournament, day),
        )
    else:
        clear_state(chat_id)
        tg_send_message(chat_id, f"Сохранил навсегда: {home_name} / {away_name}")


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
    parts.append(f"{_display_side_name(match.get('home_name'))} - {_display_side_name(match.get('away_name'))}")
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
        rows.append(
            [
                _btn(_match_label(chat_id, match, int(match["event_id"]) in selected), f"watch_toggle|{match['event_id']}"),
            ]
        )
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
        lines.append(f"  {prefix}{_display_side_name(row['home_name'])} - {_display_side_name(row['away_name'])} | {status}{tail}")
    return "\n".join(lines).strip()


def _my_matches_menu(chat_id: int, day: Optional[dt.date] = None) -> Dict[str, Any]:
    day = day or _active_day(chat_id)
    rows: List[List[Dict[str, str]]] = []
    for row in list_match_watches(chat_id, day)[:100]:
        time = _fmt_ts(chat_id, row.get("start_ts"))
        prefix = f"{time} - " if time else ""
        rows.append([_btn(_cut(f"Убрать: {prefix}{_display_side_name(row['home_name'])} - {_display_side_name(row['away_name'])}"), f"watch_del|{row['event_id']}")])
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


def _handle_text(chat_id: int, text: str, user_id: Optional[int] = None) -> None:
    raw = (text or "").strip()
    state, payload = get_state(chat_id)
    if state == "editing_match_names":
        editor_id = payload.get("editor_id")
        if editor_id and user_id and int(editor_id) != int(user_id):
            return
        if raw.lower() in {"/cancel", "cancel", "отмена"}:
            group = str(payload.get("group") or "")
            tournament = str(payload.get("tournament_name") or "")
            day = _parse_day(chat_id, payload.get("day"))
            if group and tournament:
                set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament, "day": day.isoformat()})
            else:
                clear_state(chat_id)
            tg_send_message(chat_id, "Ок, правка фамилий отменена.")
            return
        _handle_match_names_edit(chat_id, raw, payload)
        return

    if state == "editing_summary_names":
        editor_id = payload.get("editor_id")
        if editor_id and user_id and int(editor_id) != int(user_id):
            return
        summary_id = str(payload.get("summary_id") or "")
        if raw.lower() in {"/cancel", "cancel", "отмена"}:
            clear_state(chat_id)
            tg_send_message(chat_id, "Ок, правка итогов отменена.")
            return
        names = _split_names(raw)
        review = get_summary_review(summary_id)
        event = _summary_event_by_index(review or {}, int(payload.get("idx") or 0)) if review else None
        if not names:
            tg_send_message(chat_id, "Не понял фамилии. Пришли двумя строками или через /, например: Фонсека / Меншик")
            return
        if not review or not event:
            clear_state(chat_id)
            tg_send_message(chat_id, "Не нашел этот итог для редактирования.")
            return
        home_name, away_name = names
        _save_side_aliases(event, "home", home_name)
        _save_side_aliases(event, "away", away_name)
        _refresh_summary_review(summary_id)
        clear_state(chat_id)
        tg_send_message(chat_id, f"Сохранил фамилии и обновил итог: {home_name} / {away_name}")
        return

    if state == "editing_card":
        editor_id = payload.get("editor_id")
        if editor_id and user_id and int(editor_id) != int(user_id):
            return
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
        tg_send_message(chat_id, "Выбери раздел:", reply_markup=_tour_groups_menu(chat_id))
        return

    if cmd in {"/summary", "summary"}:
        tg_set_my_commands()
        clear_state(chat_id)
        tg_send_message(chat_id, "📊 Итоги игрового дня\nВыбери дату:", reply_markup=_summary_dates_menu(chat_id))
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

    tg_send_message(chat_id, "Команды:\n/today - выбрать матчи\n/summary - итоги игрового дня\n/my - мои матчи\n/tz Europe/Helsinki - сменить часовой пояс")


def _handle_callback(chat_id: int, message_id: int, cq_id: str, data: str, user_id: Optional[int] = None) -> None:
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
            payload: Dict[str, Any] = {"card_id": card_id, "field": field}
            if user_id:
                payload["editor_id"] = int(user_id)
            set_state(chat_id, "editing_card", payload)
            tg_edit_message(chat_id, message_id, _group_edit_prompt(field) if chat_id < 0 else _card_edit_prompt(field))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|root":
            clear_state(chat_id)
            tg_edit_message(chat_id, message_id, "Выбери раздел:", reply_markup=_tour_groups_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|summary":
            clear_state(chat_id)
            tg_edit_message(chat_id, message_id, "📊 Итоги игрового дня\nВыбери дату:", reply_markup=_summary_dates_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data == "menu|schedule":
            clear_state(chat_id)
            tg_edit_message(chat_id, message_id, "📅 Расписание\nВыбери дату:", reply_markup=_schedule_dates_menu(chat_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sched_date|"):
            _, day_s = data.split("|", 1)
            day = _parse_day(chat_id, day_s)
            set_state(chat_id, "schedule_day", {"day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _schedule_groups_title(chat_id, day), reply_markup=_schedule_groups_menu(day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sched_date_rel|"):
            _, rel = data.split("|", 1)
            day = _relative_day(chat_id, rel)
            set_state(chat_id, "schedule_day", {"day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _schedule_groups_title(chat_id, day), reply_markup=_schedule_groups_menu(day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sched_group|"):
            _, group, day_s = data.split("|", 2)
            day = _parse_day(chat_id, day_s)
            tours = _tournaments_map(chat_id, group, day)
            set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _tournaments_title(chat_id, group, day), reply_markup=_tournaments_menu(chat_id, group, day))
            if not tours:
                tg_answer_callback_query(cq_id, f"На {_day_label(chat_id, day)} турниров не найдено", show_alert=True)
            else:
                tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_date|"):
            _, day_s = data.split("|", 1)
            day = _parse_day(chat_id, day_s)
            set_state(chat_id, "summary_day", {"day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _summary_groups_title(chat_id, day), reply_markup=_summary_groups_menu(chat_id, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_date_rel|"):
            _, rel = data.split("|", 1)
            day = _relative_day(chat_id, rel)
            set_state(chat_id, "summary_day", {"day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _summary_groups_title(chat_id, day), reply_markup=_summary_groups_menu(chat_id, day))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_group|"):
            _, group, day_s = data.split("|", 2)
            day = _parse_day(chat_id, day_s)
            tours = _summary_tournaments_map(chat_id, group, day)
            set_state(chat_id, "summary_group", {"group": group, "day": day.isoformat()})
            tg_edit_message(chat_id, message_id, _summary_tournaments_title(chat_id, group, day), reply_markup=_summary_tournaments_menu(chat_id, group, day))
            if not tours:
                tg_answer_callback_query(cq_id, f"На {_day_label(chat_id, day)} турниров для итогов не найдено", show_alert=True)
            else:
                tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_tour|"):
            _, group, day_s, idx_s = data.split("|", 3)
            day = _parse_day(chat_id, day_s)
            idx = int(idx_s) - 1
            tours = _summary_tournaments_map(chat_id, group, day)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return
            item = tours[idx]
            set_state(
                chat_id,
                "summary_tournament",
                {
                    "group": group,
                    "day": day.isoformat(),
                    "idx": idx + 1,
                    "tournament_name": item.get("tournament_name") or "",
                    "tournament_status": item.get("tournament_status") or "",
                },
            )
            tg_edit_message(
                chat_id,
                message_id,
                _summary_tournament_title(chat_id, group, day, item),
                reply_markup=_summary_tournament_menu(group, day, idx + 1),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_publish|") or data.startswith("sum_publish_force|"):
            _, group, day_s, idx_s = data.split("|", 3)
            force = data.startswith("sum_publish_force|")
            day = _parse_day(chat_id, day_s)
            idx = int(idx_s) - 1
            tours = _summary_tournaments_map(chat_id, group, day)
            if idx < 0 or idx >= len(tours):
                tg_answer_callback_query(cq_id, "Турнир не найден", show_alert=True)
                return
            item = tours[idx]
            if not item.get("all_finished") and not force:
                total = int(item.get("matches_count") or 0)
                finished = int(item.get("finished_count") or 0)
                unfinished = max(0, total - finished)
                tg_edit_message(
                    chat_id,
                    message_id,
                    (
                        "Не все матчи этого турнира завершены.\n"
                        f"Завершено: {finished}/{total}, еще не завершено: {unfinished}.\n\n"
                        "Все равно опубликовать итоги по уже завершенным матчам?"
                    ),
                    reply_markup=_summary_publish_confirm_menu(group, day, idx + 1),
                )
                tg_answer_callback_query(cq_id)
                return
            fresh_events = _load_events_for_chat(chat_id, day, force_refresh=True)
            text, status, stage = build_daily_summary_for_tournament(
                day,
                fresh_events,
                group,
                str(item.get("tournament_name") or ""),
                str(item.get("tournament_status") or ""),
            )
            if not text:
                tg_answer_callback_query(cq_id, "Не получилось собрать текст итогов", show_alert=True)
                return
            publish_chat_id = _publish_chat_id(chat_id)
            tg_answer_callback_query(cq_id, "Собираю результаты...")
            tg_send_chat_action(publish_chat_id, "typing")
            tg_edit_message(chat_id, message_id, "Собираю результаты и отправляю итог в группу...")
            summary_id = _summary_id(day, group, str(item.get("tournament_name") or ""), str(item.get("tournament_status") or ""))
            events = summary_events_for_tournament(
                fresh_events,
                group,
                str(item.get("tournament_name") or ""),
                str(item.get("tournament_status") or ""),
            )
            save_summary_review(
                summary_id,
                publish_chat_id,
                chat_id,
                None,
                day,
                group,
                str(item.get("tournament_name") or ""),
                str(item.get("tournament_status") or ""),
                stage,
                events,
            )
            response = tg_send_message_result(publish_chat_id, text, reply_markup=_summary_review_menu(summary_id))
            message_id_sent = (response or {}).get("result", {}).get("message_id") if response else None
            if response and message_id_sent:
                set_summary_review_message(summary_id, int(message_id_sent))
                mark_daily_summary_for_tournament(day, group, str(item.get("tournament_name") or ""), status, stage)
                tg_edit_message(chat_id, message_id, "Итоги отправлены.", reply_markup=_summary_tournament_menu(group, day, idx + 1))
            else:
                tg_edit_message(chat_id, message_id, "Не смог отправить итоги.", reply_markup=_summary_tournament_menu(group, day, idx + 1))
            return

        if data.startswith("auto_sum_publish|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review or not review.get("message_id"):
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            text = _summary_review_text(review)
            if not text:
                tg_answer_callback_query(cq_id, "Не получилось собрать текст итогов", show_alert=True)
                return
            tg_answer_callback_query(cq_id, "Собираю результаты...")
            tg_send_chat_action(str(review["chat_id"]), "typing")
            tg_edit_message(str(review["chat_id"]), int(review["message_id"]), text, reply_markup=_summary_review_menu(summary_id))
            mark_daily_summary_for_tournament(
                review["day"],
                str(review.get("tour_group") or ""),
                str(review.get("tournament_name") or ""),
                str(review.get("tournament_status") or ""),
                str(review.get("stage") or ""),
            )
            return

        if data.startswith("auto_sum_skip|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review or not review.get("message_id"):
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            mark_daily_summary_for_tournament(
                review["day"],
                str(review.get("tour_group") or ""),
                str(review.get("tournament_name") or ""),
                str(review.get("tournament_status") or ""),
                str(review.get("stage") or ""),
            )
            tg_edit_message(str(review["chat_id"]), int(review["message_id"]), "Ок, этот итог не публикуем.")
            tg_answer_callback_query(cq_id, "Не публикуем")
            return

        if data.startswith("sum_edit|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review:
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            tg_edit_message(chat_id, message_id, _summary_review_text(review), reply_markup=_summary_edit_menu(summary_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_back|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review:
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            tg_edit_message(chat_id, message_id, _summary_review_text(review), reply_markup=_summary_review_menu(summary_id))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_names_menu|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review:
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            tg_edit_message(chat_id, message_id, "Выбери матч, где нужно поправить фамилии:", reply_markup=_summary_events_menu(summary_id, "sum_names", review.get("events") or []))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_move_menu|"):
            _, summary_id = data.split("|", 1)
            review = get_summary_review(summary_id)
            if not review:
                tg_answer_callback_query(cq_id, "Итог не найден", show_alert=True)
                return
            tg_edit_message(chat_id, message_id, "Выбери матч, который нужно перенести в другой раздел:", reply_markup=_summary_events_menu(summary_id, "sum_move", review.get("events") or []))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_names|"):
            _, summary_id, idx_s = data.split("|", 2)
            review = get_summary_review(summary_id)
            idx = int(idx_s)
            event = _summary_event_by_index(review or {}, idx) if review else None
            if not review or not event:
                tg_answer_callback_query(cq_id, "Матч не найден", show_alert=True)
                return
            payload: Dict[str, Any] = {"summary_id": summary_id, "idx": idx}
            if user_id:
                payload["editor_id"] = int(user_id)
            set_state(chat_id, "editing_summary_names", payload)
            tg_send_message(
                chat_id,
                (
                    "Пришли правильные фамилии в этом порядке: двумя строками или через /.\n"
                    f"Сейчас: {_display_side_name(event.get('home_name'))} / {_display_side_name(event.get('away_name'))}\n"
                    "Пример: Фонсека / Меншик\n\n"
                    "Отмена: /cancel"
                ),
            )
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_move|"):
            _, summary_id, idx_s = data.split("|", 2)
            review = get_summary_review(summary_id)
            idx = int(idx_s)
            event = _summary_event_by_index(review or {}, idx) if review else None
            if not review or not event:
                tg_answer_callback_query(cq_id, "Матч не найден", show_alert=True)
                return
            tg_edit_message(chat_id, message_id, f"Куда перенести матч?\n{_summary_event_label(event)}", reply_markup=_summary_category_menu(summary_id, idx))
            tg_answer_callback_query(cq_id)
            return

        if data.startswith("sum_setcat|"):
            _, summary_id, idx_s, category = data.split("|", 3)
            review = get_summary_review(summary_id)
            idx = int(idx_s)
            event = _summary_event_by_index(review or {}, idx) if review else None
            if not review or not event or category not in SUMMARY_CATEGORY_LABELS:
                tg_answer_callback_query(cq_id, "Не получилось перенести матч", show_alert=True)
                return
            event_id = str(int(event["event_id"]))
            overrides = review.get("overrides") or {}
            item = overrides.get(event_id) if isinstance(overrides.get(event_id), dict) else {}
            item["category"] = category
            overrides[event_id] = item
            update_summary_review_overrides(summary_id, overrides)
            _refresh_summary_review(summary_id, reply_markup=_summary_review_menu(summary_id))
            tg_answer_callback_query(cq_id, f"Перенесено: {SUMMARY_CATEGORY_LABELS[category]}")
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
                if _send_result(chat_id, match):
                    mark_match_notified(chat_id, day, event_id)
                    notice = "Матч уже завершен, результат отправлен отдельным сообщением"
                else:
                    notice = "Матч уже завершен, но плашку не удалось отправить"
            else:
                notice = "Матч добавлен. Результат придет отдельным сообщением после окончания."
            _refresh_matches_message(chat_id, message_id)
            tg_answer_callback_query(cq_id, notice)
            return

        if data.startswith("alias_match|"):
            _, event_id_s = data.split("|", 1)
            event_id = int(event_id_s)
            day = _active_day(chat_id)
            match = _find_match(chat_id, event_id, day)
            if not match:
                tg_answer_callback_query(cq_id, "Матч не найден в выбранном расписании", show_alert=True)
                return
            group, tournament, _ = _current_choice(chat_id)
            payload: Dict[str, Any] = {"event_id": event_id, "day": day.isoformat()}
            if group:
                payload["group"] = group
            if tournament:
                payload["tournament_name"] = tournament
            if user_id:
                payload["editor_id"] = int(user_id)
            set_state(chat_id, "editing_match_names", payload)
            tg_edit_message(
                chat_id,
                message_id,
                _match_names_edit_prompt(chat_id, match),
                reply_markup=_kb([[_btn("Отмена", "alias_cancel")]]),
            )
            tg_answer_callback_query(cq_id)
            return

        if data == "alias_cancel":
            state, payload = get_state(chat_id)
            group = str((payload or {}).get("group") or "")
            tournament = str((payload or {}).get("tournament_name") or "")
            day = _parse_day(chat_id, (payload or {}).get("day"))
            if group and tournament:
                set_state(chat_id, "picked_tournament", {"group": group, "tournament_name": tournament, "day": day.isoformat()})
                tg_edit_message(chat_id, message_id, _matches_title(chat_id, group, tournament, day), reply_markup=_matches_menu(chat_id, group, tournament, day))
            else:
                clear_state(chat_id)
                tg_edit_message(chat_id, message_id, "Правка фамилий отменена.", reply_markup=_tour_groups_menu(chat_id))
            tg_answer_callback_query(cq_id)
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
            if _send_result(chat_id, match):
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
                user_id = int((msg.get("from") or {}).get("id") or 0)
                state, payload = get_state(chat_id)
                editor_id = payload.get("editor_id") if state == "editing_card" else None
                if editor_id and user_id and int(editor_id) != int(user_id):
                    pass
                elif state == "editing_card" and str(payload.get("field") or "") == "photo" and _handle_card_photo_upload(chat_id, msg, payload):
                    pass
                else:
                    _handle_text(chat_id, msg.get("text") or msg.get("caption") or "", user_id=user_id)
            elif "callback_query" in upd:
                cq = upd["callback_query"] or {}
                msg = cq.get("message") or {}
                chat_id = int((msg.get("chat") or {})["id"])
                user_id = int((cq.get("from") or {}).get("id") or 0)
                _handle_callback(chat_id, int(msg["message_id"]), cq.get("id") or "", cq.get("data") or "", user_id=user_id)
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
