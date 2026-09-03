from __future__ import annotations

import copy
import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from db_pg import get_events_cache
from providers import sofascore as ss
import tournament_session_store as store


_INSTALLED = False


def _status_rank(event: Dict[str, Any]) -> int:
    return {
        "finished": 6,
        "retired": 6,
        "walkover": 6,
        "cancelled": 6,
        "interrupted": 4,
        "inprogress": 3,
        "notstarted": 2,
    }.get(ss.status_type(event), 1)


def _merged_cached_events(source_day: dt.date, current_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[int, Dict[str, Any]] = {}

    def add(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            try:
                event_id = int(row.get("event_id") or 0)
            except Exception:
                continue
            if not event_id:
                continue
            old = by_id.get(event_id)
            if old is None or _status_rank(row) >= _status_rank(old):
                by_id[event_id] = copy.deepcopy(row)

    # Current poll snapshot is authoritative for its source date.
    add(current_events)
    # A tournament game day can cross the source calendar boundary. Merge nearby
    # cached source days before deciding whether an automatic summary is complete.
    for offset in (-2, -1, 0, 1, 2):
        day = source_day + dt.timedelta(days=offset)
        try:
            data = get_events_cache(day) or {"events": []}
            add(ss.normalize_events(data))
        except Exception as exc:
            print(f"[summary-safe] cache read failed day={day}: {exc}")
    return list(by_id.values())


def _profiled_event(event: Dict[str, Any]) -> tuple[Dict[str, Any], dt.date, Dict[str, Any]] | None:
    try:
        timestamp = int(event.get("start_ts") or 0)
    except Exception:
        timestamp = 0
    if not timestamp:
        return None

    source = str(event.get("tournament_source_name") or event.get("tournament_name") or "")
    if not source:
        return None
    profile = store.get_profile(source)
    try:
        timezone = ZoneInfo(str(profile.get("tz") or store.DEFAULT_TZ))
        cutoff = int(profile.get("cutoff", store.DEFAULT_CUTOFF))
        local_start = dt.datetime.fromtimestamp(timestamp, timezone)
        game_day = (local_start - dt.timedelta(minutes=cutoff)).date()
    except Exception:
        return None

    row = copy.deepcopy(event)
    row["tournament_source_name"] = source
    row["tournament_name"] = str(profile.get("name") or source)
    row["tournament_timezone"] = str(profile.get("tz") or store.DEFAULT_TZ)
    row["tournament_cutoff_minutes"] = cutoff
    row["session_day"] = game_day.isoformat()
    return row, game_day, profile


def _session_closed(game_day: dt.date, profile: Dict[str, Any]) -> bool:
    try:
        timezone = ZoneInfo(str(profile.get("tz") or store.DEFAULT_TZ))
        cutoff = int(profile.get("cutoff", store.DEFAULT_CUTOFF))
        end = dt.datetime.combine(game_day, dt.time.min, tzinfo=timezone) + dt.timedelta(
            days=1, minutes=cutoff
        )
        return dt.datetime.now(timezone) >= end
    except Exception:
        return False


def install(daily_summary: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    old_publish = daily_summary.publish_daily_summaries

    def approval_text(day, tournament, status, matches_count, *, total_matches, unfinished_count):
        title = " · ".join(part for part in (status, tournament) if part)
        return (
            "Игровой день турнира завершён.\n"
            f"Завершено матчей: {matches_count} из {total_matches}.\n"
            f"{day:%d.%m.%Y} · {title or 'турнир'}\n\n"
            "Опубликовать результаты?"
        )

    daily_summary._summary_approval_text = approval_text

    def publish_complete_days(source_day, events, bot_token, chat_id):
        if not daily_summary.enabled() or not bot_token or not chat_id:
            return 0

        merged = _merged_cached_events(source_day, list(events or []))
        grouped: Dict[tuple[dt.date, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
        profiles: Dict[tuple[dt.date, str, str, str], Dict[str, Any]] = {}

        for event in merged:
            if not daily_summary._is_target_event(event, automatic=True):
                continue
            prepared = _profiled_event(event)
            if not prepared:
                continue
            row, game_day, profile = prepared
            key = (
                game_day,
                str(row.get("tour_group") or ""),
                str(row.get("tournament_name") or ""),
                str(row.get("tournament_status") or ""),
            )
            grouped[key].append(row)
            profiles[key] = profile

        sent = 0
        for key, rows in grouped.items():
            game_day, group, tournament, status = key
            profile = profiles[key]

            # Never offer a partial/"almost finished" day. We wait until the
            # tournament-local game-day window itself is closed first.
            if not _session_closed(game_day, profile):
                continue

            # Deduplicate once more inside the tournament day and use the most
            # advanced source status for each event.
            by_id: Dict[int, Dict[str, Any]] = {}
            for row in rows:
                event_id = int(row.get("event_id") or 0)
                old = by_id.get(event_id)
                if old is None or _status_rank(row) >= _status_rank(old):
                    by_id[event_id] = row
            complete_rows = list(by_id.values())
            if not complete_rows:
                continue

            unfinished = [row for row in complete_rows if not ss.is_finished(row)]
            if unfinished:
                print(
                    f"[summary-safe] skip incomplete day={game_day} tournament={tournament} "
                    f"finished={len(complete_rows)-len(unfinished)}/{len(complete_rows)}"
                )
                continue

            # The legacy publisher is now safe because it receives exactly one
            # complete tournament game day, not a Flashscore calendar-day slice.
            sent += int(old_publish(game_day, complete_rows, bot_token, chat_id) or 0)

        return sent

    daily_summary.publish_daily_summaries = publish_complete_days
    _INSTALLED = True
