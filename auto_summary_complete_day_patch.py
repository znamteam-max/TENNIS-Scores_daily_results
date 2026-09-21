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
        "cancelled": 5,
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

    add(current_events)
    for offset in (-2, -1, 0, 1, 2):
        day = source_day + dt.timedelta(days=offset)
        try:
            data = get_events_cache(day) or {"events": []}
            add(ss.normalize_events(data))
        except Exception as exc:
            print(f"[summary-safe] cache read failed day={day}: {exc}")

    rows = list(by_id.values())
    try:
        apply_team_context = getattr(ss, "apply_team_context", None)
        if callable(apply_team_context):
            rows = list(apply_team_context(rows))
    except Exception as exc:
        print(f"[summary-safe] team context failed: {exc}")
    return rows


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
    base_profile = store.get_profile(source)

    # Distributed team events (Davis Cup) have one source tournament but many
    # venues/timezones. Normal tournaments continue to use their saved profile.
    team_event = bool(event.get("team_event"))
    timezone_name = (
        str(event.get("tournament_timezone") or "")
        if team_event
        else str(base_profile.get("tz") or store.DEFAULT_TZ)
    )
    if not timezone_name:
        timezone_name = str(base_profile.get("tz") or store.DEFAULT_TZ)
    try:
        cutoff = int(
            event.get("tournament_cutoff_minutes", base_profile.get("cutoff", store.DEFAULT_CUTOFF))
            if team_event
            else base_profile.get("cutoff", store.DEFAULT_CUTOFF)
        )
        timezone = ZoneInfo(timezone_name)
        local_start = dt.datetime.fromtimestamp(timestamp, timezone)
        game_day = (local_start - dt.timedelta(minutes=cutoff)).date()
    except Exception:
        return None

    row = copy.deepcopy(event)
    row["tournament_source_name"] = source
    row["tournament_name"] = (
        str(event.get("tournament_name") or source)
        if team_event
        else str(base_profile.get("name") or source)
    )
    row["tournament_timezone"] = timezone_name
    row["tournament_cutoff_minutes"] = cutoff
    row["session_day"] = game_day.isoformat()

    profile = dict(base_profile)
    profile["tz"] = timezone_name
    profile["cutoff"] = cutoff
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

    def result_ready(event: Dict[str, Any]) -> bool:
        # "cancelled" used to count as finished, which allowed prompts like 5/13.
        # Automatic summaries are now allowed only for rows that have an actual
        # winner and a complete score line. Manual /summary remains the force path.
        try:
            return bool(ss.has_result_winner(event) and daily_summary._result_line(event))
        except Exception:
            return False

    def publish_complete_days(source_day, events, bot_token, chat_id):
        if not daily_summary.enabled() or not bot_token or not chat_id:
            return 0

        merged = _merged_cached_events(source_day, list(events or []))
        grouped: Dict[tuple[dt.date, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
        profiles: Dict[tuple[dt.date, str, str, str], List[Dict[str, Any]]] = defaultdict(list)

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
            profiles[key].append(profile)

        sent = 0
        for key, rows in grouped.items():
            game_day, group, tournament, status = key

            # A distributed competition can have several venue timezones in the
            # same "tournament" bucket. Do not close the day until every local
            # venue window belonging to that game day is closed.
            if not profiles[key] or not all(_session_closed(game_day, profile) for profile in profiles[key]):
                continue

            by_id: Dict[int, Dict[str, Any]] = {}
            for row in rows:
                event_id = int(row.get("event_id") or 0)
                old = by_id.get(event_id)
                if old is None or _status_rank(row) >= _status_rank(old):
                    by_id[event_id] = row
            complete_rows = list(by_id.values())
            if not complete_rows:
                continue

            unfinished = [row for row in complete_rows if not result_ready(row)]
            if unfinished:
                ready = len(complete_rows) - len(unfinished)
                print(
                    f"[summary-safe] skip incomplete day={game_day} tournament={tournament} "
                    f"result_ready={ready}/{len(complete_rows)}"
                )
                continue

            # At this point the legacy publisher receives only real result rows.
            # Therefore its approval text can only be N/N, never 5/13 or 7/13.
            sent += int(old_publish(game_day, complete_rows, bot_token, chat_id) or 0)

        return sent

    daily_summary.publish_daily_summaries = publish_complete_days
    _INSTALLED = True
