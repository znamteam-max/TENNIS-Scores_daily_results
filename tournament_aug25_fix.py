from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import tournament_session_store as store


COMMANDS = {"/start", "/today", "/summary", "/my", "/tz"}
UNKNOWN_STAGES = {"", "Стадия не определена", "Раунд не найден"}
TZ_ALIASES = {
    "mexico": "America/Monterrey",
    "mexico/monterrey": "America/Monterrey",
    "mexico / monterrey": "America/Monterrey",
    "monterrey": "America/Monterrey",
    "монтеррей": "America/Monterrey",
    "мексика": "America/Monterrey",
    "toronto": "America/Toronto",
    "торонто": "America/Toronto",
    "montreal": "America/Toronto",
    "монреаль": "America/Toronto",
    "new york": "America/New_York",
    "new_york": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "los_angeles": "America/Los_Angeles",
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai",
}
TZ_PRESETS = [
    ("🇲🇽 Монтеррей", "America/Monterrey"),
    ("🇨🇦 Торонто / Монреаль", "America/Toronto"),
    ("🇺🇸 Нью-Йорк", "America/New_York"),
    ("🇺🇸 Лос-Анджелес", "America/Los_Angeles"),
    ("🇬🇧 Лондон", "Europe/London"),
    ("🇫🇷 Париж", "Europe/Paris"),
    ("🇯🇵 Токио", "Asia/Tokyo"),
    ("🇨🇳 Шанхай", "Asia/Shanghai"),
]


def _is_monterrey(value: Any) -> bool:
    text = store.norm(value)
    return "monterrey" in text or "монтеррей" in text


def _normalize_tz(value: str, source: str = "") -> str:
    raw = " ".join(str(value or "").strip().split())
    key = raw.lower()
    compact = key.replace(" ", "")
    if key in TZ_ALIASES:
        return TZ_ALIASES[key]
    if compact in {"mexico/monterrey", "mexico/monterrey"}:
        return "America/Monterrey"
    if _is_monterrey(source) and key in {"mexico", "мексика", "monterrey", "монтеррей"}:
        return "America/Monterrey"
    return raw


def _command(text: str) -> str:
    raw = str(text or "").strip().split(" ", 1)[0]
    raw = raw.replace("\\@", "@").replace("\\_", "_")
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return raw.lower()


def install(module: Any) -> None:
    old_callback = module._handle_callback
    old_text = module._handle_text
    old_guess_timezone = store.guess_timezone
    old_get_profile = store.get_profile

    def guess_timezone(name: str) -> str:
        if _is_monterrey(name):
            return "America/Monterrey"
        return old_guess_timezone(name)

    def get_profile(source: str) -> Dict[str, Any]:
        profile = dict(old_get_profile(source))
        # Profiles created before the Monterrey rule inherited the European default.
        # Correct only the untouched default; a deliberately saved timezone still wins.
        if _is_monterrey(source) and str(profile.get("tz") or "") == str(store.DEFAULT_TZ):
            profile["tz"] = "America/Monterrey"
        return profile

    store.guess_timezone = guess_timezone
    store.get_profile = get_profile
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    def profile_payload(chat_id: int) -> Dict[str, Any]:
        return dict((module.get_state(chat_id)[1] or {}))

    def tz_menu(source: str):
        profile = store.get_profile(source)
        recommended = store.guess_timezone(source)
        rows = [[module._btn(f"✅ Авто: {recommended}", f"tzpreset|{recommended}")]]
        for label, tz in TZ_PRESETS:
            if tz == recommended:
                continue
            rows.append([module._btn(f"{label} · {tz}", f"tzpreset|{tz}")])
        rows.extend(
            [
                [module._btn("⌨️ Ввести вручную", "tzmanual")],
                [module._btn("Отмена", "tzcancel")],
            ]
        )
        return module._kb(rows)

    def restore_tournament(chat_id: int, payload: Dict[str, Any], *, edit_message_id: int | None = None) -> None:
        source = str(payload.get("source") or payload.get("tournament_name") or "")
        group = str(payload.get("group") or "men")
        day = module._parse_day(chat_id, payload.get("day"))
        tournament = str(store.get_profile(source).get("name") or payload.get("tournament_name") or source)
        module.set_state(
            chat_id,
            "picked_tournament",
            {"group": group, "tournament_name": tournament, "day": day.isoformat()},
        )
        text = module._matches_title(chat_id, group, tournament, day)
        markup = module._matches_menu(chat_id, group, tournament, day)
        if edit_message_id is not None:
            module.tg_edit_message(chat_id, edit_message_id, text, reply_markup=markup)
        else:
            module.tg_send_message(chat_id, text, reply_markup=markup)

    def save_timezone(chat_id: int, payload: Dict[str, Any], raw_tz: str, *, edit_message_id: int | None = None) -> str:
        source = str(payload.get("source") or payload.get("tournament_name") or "")
        timezone = _normalize_tz(raw_tz, source)
        ZoneInfo(timezone)
        value = store.save_profile(source, tz=timezone)
        try:
            store._CACHE.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
        if edit_message_id is not None:
            restore_tournament(chat_id, payload, edit_message_id=edit_message_id)
        else:
            module.tg_send_message(chat_id, f"Timezone сохранён: {value['name']} · {timezone}")
            restore_tournament(chat_id, payload)
        return timezone

    def _scan_unknown(events: List[Dict[str, Any]]) -> tuple[int, int, Dict[str, List[int]]]:
        targets = [event for event in events if str(event.get("stage") or "") in UNKNOWN_STAGES]
        if not targets:
            return 0, 0, {}
        try:
            import tournament_stage_full_scan_patch as scanner
        except Exception:
            return 0, len(targets), {}
        detected: Dict[str, List[int]] = defaultdict(list)
        workers = min(8, max(1, len(targets)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(scanner._stage_from_page, event) for event in targets]  # type: ignore[attr-defined]
            for future in as_completed(futures):
                try:
                    event_id, stage = future.result()
                except Exception:
                    continue
                stage = store.normalize_stage(stage)
                if event_id and stage:
                    detected[stage].append(int(event_id))
        found = 0
        for stage, event_ids in detected.items():
            store.save_stage(event_ids, stage)
            found += len(event_ids)
        if found:
            try:
                store._CACHE.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
        return found, len(targets), detected

    def ensure_summary_stages(chat_id: int, group: str, day, index: int) -> None:
        items = module._summary_tournaments_map(chat_id, group, day)
        if index < 0 or index >= len(items):
            return
        item = items[index]
        tournament = str(item.get("tournament_name") or "")
        status = str(item.get("tournament_status") or "")
        events = module._load_events_for_chat(chat_id, day, force_refresh=True)
        rows = [
            event
            for event in events
            if str(event.get("tour_group") or "") == group
            and str(event.get("tournament_name") or "") == tournament
            and (not status or str(event.get("tournament_status") or "") == status)
            and str(event.get("session_day") or day.isoformat()) == day.isoformat()
        ]
        _scan_unknown(rows)

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        try:
            if data == "tprof_edit|tz":
                payload = profile_payload(chat_id)
                source = str(payload.get("source") or payload.get("tournament_name") or "")
                current = str(store.get_profile(source).get("tz") or "")
                module.set_state(chat_id, "tournament_profile", payload)
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    f"Timezone турнира: {source}\nСейчас: {current}\n\nВыбери город/зону:",
                    reply_markup=tz_menu(source),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data.startswith("tzpreset|"):
                timezone = data.split("|", 1)[1]
                payload = profile_payload(chat_id)
                saved = save_timezone(chat_id, payload, timezone, edit_message_id=message_id)
                module.tg_answer_callback_query(cq_id, f"Сохранено: {saved}")
                return

            if data == "tzmanual":
                payload = profile_payload(chat_id)
                source = str(payload.get("source") or payload.get("tournament_name") or "")
                module.set_state(chat_id, "tprof_tz_manual", payload)
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    (
                        f"Турнир: {source}\n"
                        "Пришли timezone одним сообщением.\n"
                        "Например: America/Monterrey.\n\n"
                        "Можно также написать просто: Monterrey, Mexico, Toronto, New York, London, Tokyo."
                    ),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data == "tzcancel":
                payload = profile_payload(chat_id)
                restore_tournament(chat_id, payload, edit_message_id=message_id)
                module.tg_answer_callback_query(cq_id, "Отменено")
                return

            if data.startswith(("sum_tour|", "sum_publish|", "sum_publish_force|")):
                parts = data.split("|")
                if len(parts) >= 4:
                    group = parts[1]
                    day = module._parse_day(chat_id, parts[2])
                    index = int(parts[3]) - 1
                    # Do not require a separate manual scan before publishing a summary.
                    ensure_summary_stages(chat_id, group, day, index)
        except Exception as exc:
            module.tg_answer_callback_query(cq_id, f"Ошибка: {exc}", show_alert=True)
            return
        return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

    module._handle_callback = callback

    def text_handler(chat_id, text, user_id=None):
        state, payload = module.get_state(chat_id)
        payload = dict(payload or {})
        cmd = _command(text)

        # Commands always escape an unfinished edit state. Previously /summary was
        # accidentally parsed as a timezone value and the user got trapped there.
        if cmd in COMMANDS:
            module.clear_state(chat_id)
            return old_text(chat_id, text, user_id=user_id)

        if state in {"tprof_tz", "tprof_tz_manual"}:
            if str(text or "").strip().lower() in {"отмена", "cancel", "/cancel"}:
                restore_tournament(chat_id, payload)
                return
            try:
                save_timezone(chat_id, payload, str(text or ""))
            except Exception:
                source = str(payload.get("source") or payload.get("tournament_name") or "")
                recommended = store.guess_timezone(source)
                module.tg_send_message(
                    chat_id,
                    (
                        f"Не распознал timezone. Для «{source}» рекомендую {recommended}.\n"
                        "Редактирование отменено, чтобы бот не воспринимал следующие сообщения как timezone."
                    ),
                )
                restore_tournament(chat_id, payload)
            return

        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
