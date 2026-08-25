from __future__ import annotations

import datetime as dt
from typing import Any, Dict
from zoneinfo import ZoneInfo

import tournament_session_store as store


_AUTO_TZ_RULES = (
    (("monterrey", "монтеррей"), "America/Monterrey"),
    (("us open", "u.s. open", "открытый чемпионат сша", "new york", "нью-йорк", "flushing"), "America/New_York"),
    (("cincinnati", "цинциннати", "washington", "вашингтон", "miami", "майами"), "America/New_York"),
    (("montreal", "монреаль", "toronto", "торонто"), "America/Toronto"),
    (("indian wells", "индиан-уэллс", "los angeles", "лос-анджелес"), "America/Los_Angeles"),
    (("wimbledon", "уимблдон", "london", "лондон", "eastbourne", "истборн"), "Europe/London"),
    (("roland garros", "french open", "ролан гаррос", "paris", "париж"), "Europe/Paris"),
    (("shanghai", "шанхай", "beijing", "пекин", "wuhan", "ухань", "ningbo", "нинбо"), "Asia/Shanghai"),
    (("tokyo", "токио"), "Asia/Tokyo"),
    (("seoul", "сеул"), "Asia/Seoul"),
    (("dubai", "дубай", "abu dhabi", "абу-даби"), "Asia/Dubai"),
    (("doha", "доха"), "Asia/Qatar"),
    (("australian open", "австрали", "melbourne", "мельбурн"), "Australia/Melbourne"),
)

_COMMON_INSTALLED = False
_WEBHOOK_INSTALLED = False


def _recommended_timezone(name: Any) -> str:
    text = store.norm(name)
    for needles, timezone in _AUTO_TZ_RULES:
        if any(needle in text for needle in needles):
            return timezone
    return ""


def _parse_day(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if value:
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except Exception:
            pass
    return None


def result_day(event: Dict[str, Any]) -> dt.date | None:
    for key in ("session_day", "game_day", "day"):
        parsed = _parse_day(event.get(key))
        if parsed:
            return parsed

    try:
        timestamp = int(event.get("start_ts") or 0)
    except Exception:
        timestamp = 0
    if not timestamp:
        return None

    source = str(
        event.get("tournament_source_name")
        or event.get("tournament_name")
        or ""
    )
    profile = store.get_profile(source)
    timezone = str(event.get("tournament_timezone") or profile.get("tz") or store.DEFAULT_TZ)
    try:
        cutoff = int(event.get("tournament_cutoff_minutes", profile.get("cutoff", store.DEFAULT_CUTOFF)))
    except Exception:
        cutoff = store.DEFAULT_CUTOFF
    try:
        return (
            dt.datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
            - dt.timedelta(minutes=cutoff)
        ).date()
    except Exception:
        return None


def _add_result_day(text: str, day: dt.date | None) -> str:
    if not text or not day:
        return text
    line = f"📅 Игровой день: {day:%d.%m.%Y}"
    if line in text or day.strftime("%d.%m.%Y") in text.split("\n", 3)[0:3]:
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("📊 Результаты игрового дня"):
        lines.insert(1, line)
    elif len(lines) >= 2:
        lines.insert(2, line)
    else:
        lines.insert(0, line)
    return "\n".join(lines)


def install_common() -> None:
    global _COMMON_INSTALLED
    if _COMMON_INSTALLED:
        return

    from providers import sofascore as ss
    import daily_summary

    old_result_message = ss.result_message

    def result_message(event: Dict[str, Any], include_stats: bool = True) -> str:
        return _add_result_day(old_result_message(event, include_stats=include_stats), result_day(event))

    ss.result_message = result_message

    old_summary_text = daily_summary._build_summary_text

    def summary_text(day, *args, **kwargs):
        text = old_summary_text(day, *args, **kwargs)
        return _add_result_day(text, _parse_day(day))

    daily_summary._build_summary_text = summary_text
    _COMMON_INSTALLED = True


def install_webhook(module: Any) -> None:
    global _WEBHOOK_INSTALLED
    if _WEBHOOK_INSTALLED:
        return

    old_text = module._handle_text
    old_guess_timezone = store.guess_timezone
    old_get_profile = store.get_profile

    def guess_timezone(name: str) -> str:
        return _recommended_timezone(name) or old_guess_timezone(name)

    def get_profile(source: str) -> Dict[str, Any]:
        profile = dict(old_get_profile(source))
        recommended = _recommended_timezone(source)
        current = str(profile.get("tz") or "")
        # Repair only untouched/default profiles. Explicitly saved user overrides win.
        if recommended and current in {"", str(store.DEFAULT_TZ), "Europe/Helsinki"}:
            profile["tz"] = recommended
        return profile

    store.guess_timezone = guess_timezone
    store.get_profile = get_profile
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    def text_handler(chat_id, text, user_id=None):
        raw = str(text or "").strip()
        first = raw.split(" ", 1)[0].replace("\\@", "@").replace("\\_", "_")
        if "@" in first:
            first = first.split("@", 1)[0]
        command = first.lower()
        if command == "/cancel" or raw.lower() in {"cancel", "отмена"}:
            module.clear_state(chat_id)
            module.tg_send_message(chat_id, "Отменено. Режим редактирования сброшен.")
            return
        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
    _WEBHOOK_INSTALLED = True
