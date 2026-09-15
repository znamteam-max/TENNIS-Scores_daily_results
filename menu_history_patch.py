from __future__ import annotations

import asyncio
import copy
import datetime as dt
import time
from typing import Any
from zoneinfo import ZoneInfo

import tournament_session_store as store
from providers import sofascore as ss


_INSTALLED = False
_HISTORY_CACHE: dict[tuple[int, str], tuple[float, list[dict[str, Any]]]] = {}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _pair_key(event: dict[str, Any]) -> tuple[str, str]:
    names = sorted((_norm(event.get("home_name")), _norm(event.get("away_name"))))
    return names[0], names[1]


def _score_total(event: dict[str, Any], side: str) -> int:
    raw = event.get("raw") or {}
    score = raw.get("homeScore" if side == "home" else "awayScore") or {}
    value = score.get("current")
    if value is None:
        value = score.get("display")
    try:
        return int(float(value))
    except Exception:
        return 0


def _recover_grand_slam_group(event: dict[str, Any]) -> None:
    if event.get("tour_group") in {"men", "women"}:
        return
    if int(event.get("tournament_sort_rank", 9)) != 0:
        return

    # A completed Grand Slam match is unambiguous from the number of sets won:
    # men's singles is best-of-five, women's singles best-of-three.
    maximum = max(_score_total(event, "home"), _score_total(event, "away"))
    if maximum >= 3:
        event["tour_group"] = "men"
        event["tour_label"] = ss.tour_label("men")
        if event.get("category") == "Other":
            event["category"] = "ATP"
    elif maximum == 2 and ss.has_result_winner(event):
        event["tour_group"] = "women"
        event["tour_label"] = ss.tour_label("women")
        if event.get("category") == "Other":
            event["category"] = "WTA"


def _session_day(event: dict[str, Any], requested_day: dt.date) -> dt.date:
    source = str(event.get("tournament_name") or "Турнир")
    profile = store.get_profile(source)
    try:
        timestamp = int(event.get("start_ts") or 0)
    except Exception:
        timestamp = 0
    if not timestamp:
        return requested_day
    try:
        return (
            dt.datetime.fromtimestamp(timestamp, ZoneInfo(str(profile["tz"])))
            - dt.timedelta(minutes=int(profile["cutoff"]))
        ).date()
    except Exception:
        return requested_day


def _prepare_fallback_event(event: dict[str, Any], day: dt.date) -> dict[str, Any] | None:
    from tournament_sep6_validation_patch import is_adult_singles_event

    row = copy.deepcopy(event)
    _recover_grand_slam_group(row)
    if row.get("tour_group") not in {"men", "women"}:
        return None
    if not is_adult_singles_event(row):
        return None

    # The fallback is only for main-tour/Grand-Slam holes. Flashscore remains
    # authoritative for Challenger/ITF so we do not duplicate those menus.
    category = str(row.get("category") or "")
    rank = int(row.get("tournament_sort_rank", 9))
    if category not in {"ATP", "WTA"} and rank > 3:
        return None
    if _session_day(row, day) != day:
        return None

    source = str(row.get("tournament_name") or "Турнир")
    profile = store.get_profile(source)
    row.update(
        {
            "tournament_source_name": source,
            "tournament_key": profile["key"],
            "tournament_name": profile["name"],
            "tournament_timezone": profile["tz"],
            "tournament_cutoff_minutes": profile["cutoff"],
            "session_day": day.isoformat(),
            "_source_day": day.isoformat(),
        }
    )
    return row


async def _fallback_rows(day: dt.date) -> list[dict[str, Any]]:
    from providers import espn_fallback
    from providers import sofascore_fallback

    results = await asyncio.gather(
        sofascore_fallback.events_by_date(day),
        espn_fallback.events_by_date(day),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            print(f"[history] fallback source failed day={day}: {result}")
            continue
        try:
            rows.extend(ss.normalize_events(result or {"events": []}))
        except Exception as exc:
            print(f"[history] fallback normalization failed day={day}: {exc}")
    return rows


def _merge_history(primary: list[dict[str, Any]], day: dt.date) -> list[dict[str, Any]]:
    try:
        fallback = asyncio.run(_fallback_rows(day))
    except Exception as exc:
        print(f"[history] fallback load failed day={day}: {exc}")
        return primary

    rows = [copy.deepcopy(event) for event in primary]
    known_pairs = {_pair_key(event) for event in rows if all(_pair_key(event))}
    added = 0
    for event in fallback:
        prepared = _prepare_fallback_event(event, day)
        if not prepared:
            continue
        key = _pair_key(prepared)
        if all(key) and key in known_pairs:
            continue
        if all(key):
            known_pairs.add(key)
        rows.append(prepared)
        added += 1

    if added:
        store.apply_stages(rows)
        rows.sort(
            key=lambda event: (
                int(event.get("tournament_sort_rank", 9)),
                str(event.get("tour_group")),
                str(event.get("tournament_name")).lower(),
                store.STAGE_ORDER.get(str(event.get("stage")), 90),
                int(event.get("start_ts") or 0),
                int(event.get("event_id") or 0),
            )
        )
        print(f"[history] recovered fallback matches day={day} added={added}")
    return rows


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    old_root_menu = module._tour_groups_menu
    old_load_events = module._load_events_for_chat

    def root_menu(chat_id: int):
        """Make the difference between today's shortcuts and date navigation explicit."""
        markup = old_root_menu(chat_id)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated_row = []
            for button in row:
                item = dict(button)
                data = str(item.get("callback_data") or "")
                if data == "menu|schedule":
                    item["text"] = "📅 Другие даты"
                elif data == "group|men":
                    item["text"] = "Мужчины — сегодня"
                elif data == "group|women":
                    item["text"] = "Женщины — сегодня"
                updated_row.append(item)
            rows.append(updated_row)
        return module._kb(rows)

    def load_events(chat_id: int, day=None, force_refresh: bool = False):
        day = day or module._active_day(chat_id)
        today = module._today(chat_id)
        historical = today - dt.timedelta(days=14) <= day < today
        cache_key = (int(chat_id), day.isoformat())
        if historical and not force_refresh:
            cached = _HISTORY_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < 300:
                return copy.deepcopy(cached[1])

        # Historical schedules must not rely forever on a partial events_cache
        # snapshot saved while that day was still in progress.
        primary = list(
            old_load_events(
                chat_id,
                day,
                force_refresh=bool(force_refresh or historical),
            )
        )
        if historical:
            primary = _merge_history(primary, day)
            _HISTORY_CACHE[cache_key] = (time.time(), copy.deepcopy(primary))
        return primary

    def _recent_date_rows(chat_id: int, prefix: str):
        today = module._today(chat_id)
        days = [today - dt.timedelta(days=offset) for offset in range(7)]
        rows = [
            [
                module._btn("Сегодня", f"{prefix}|{days[0].isoformat()}"),
                module._btn("Вчера", f"{prefix}|{days[1].isoformat()}"),
            ]
        ]
        for start in range(2, 7, 2):
            row = [
                module._btn(day.strftime("%d.%m"), f"{prefix}|{day.isoformat()}")
                for day in days[start : start + 2]
            ]
            if row:
                rows.append(row)
        return rows

    def schedule_dates_menu(chat_id: int):
        rows = _recent_date_rows(chat_id, "sched_date")
        rows.append([module._btn("Назад", "menu|root")])
        return module._kb(rows)

    def summary_dates_menu(chat_id: int):
        rows = _recent_date_rows(chat_id, "sum_date")
        rows.append([module._btn("Назад", "menu|summary")])
        return module._kb(rows)

    module._tour_groups_menu = root_menu
    module._load_events_for_chat = load_events
    module._schedule_dates_menu = schedule_dates_menu
    module._summary_dates_menu = summary_dates_menu
    _INSTALLED = True
