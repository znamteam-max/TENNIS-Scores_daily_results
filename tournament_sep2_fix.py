from __future__ import annotations

import datetime as dt
import os
import time
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import db_pg
import tournament_session_store as store
from providers import sofascore as ss

_STORE_INSTALLED = False
_WEBHOOK_INSTALLED = False
_POLL_INSTALLED = False


def _watch_max_age_hours() -> int:
    try:
        return max(12, int(os.getenv("RESULT_WATCH_MAX_AGE_HOURS", "36")))
    except Exception:
        return 36


def _expire_stale_watches() -> int:
    """Prevent an old selected match from suddenly publishing days later."""
    try:
        seconds = _watch_max_age_hours() * 3600
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                update match_watches
                   set notified_at=now()
                 where notified_at is null
                   and (
                        (start_ts is not null and start_ts < extract(epoch from now())::bigint - %s)
                        or (start_ts is null and day < current_date - interval '2 days')
                   )
                """,
                (seconds,),
            )
            expired = int(cur.rowcount or 0)
        if expired:
            print(f"[watch-safety] expired stale watches={expired} max_age_hours={_watch_max_age_hours()}")
        return expired
    except Exception as exc:
        print(f"[watch-safety] stale-watch cleanup failed: {exc}")
        return 0


def _watch_is_stale(row: Dict[str, Any]) -> bool:
    try:
        start_ts = int(row.get("start_ts") or 0)
    except Exception:
        start_ts = 0
    if start_ts:
        return time.time() - start_ts > _watch_max_age_hours() * 3600
    day = row.get("session_day") or row.get("day")
    try:
        parsed = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day)[:10])
        return parsed < dt.date.today() - dt.timedelta(days=2)
    except Exception:
        return False


def install_store_safety() -> None:
    global _STORE_INSTALLED
    if _STORE_INSTALLED:
        return

    old_pending_days = store.pending_source_days
    old_pending_watches = store.pending_watches

    def pending_days():
        _expire_stale_watches()
        return old_pending_days()

    def pending_watches(source_day):
        _expire_stale_watches()
        return [row for row in old_pending_watches(source_day) if not _watch_is_stale(row)]

    store.pending_source_days = pending_days
    store.pending_watches = pending_watches
    _STORE_INSTALLED = True


def install_poll_safety(gha_worker: Any) -> None:
    """Make fallback result matching conservative: wrong/no result is better than a wrong card."""
    global _POLL_INSTALLED
    if _POLL_INSTALLED:
        return

    old_candidate_score = gha_worker._candidate_score

    def candidate_score(watch: Dict[str, Any], fallback: Dict[str, Any]):
        score, reversed_sides = old_candidate_score(watch, fallback)
        if not score:
            return 0, False

        # A fallback must be the same match in time, not merely the same surnames.
        if not gha_worker._time_match(watch.get("start_ts"), fallback.get("start_ts"), max_delta_seconds=8 * 3600):
            return 0, False

        watch_tournament = watch.get("source_tournament_name") or watch.get("tournament_name")
        fallback_tournament = fallback.get("tournament_name")
        if watch_tournament and fallback_tournament and not gha_worker._tournament_match(watch_tournament, fallback_tournament):
            return 0, False

        return score + 10, reversed_sides

    gha_worker._candidate_score = candidate_score
    _POLL_INSTALLED = True


def _event_game_day(event: Dict[str, Any], selected_day: dt.date) -> dt.date | None:
    try:
        timestamp = int(event.get("start_ts") or 0)
    except Exception:
        timestamp = 0
    if not timestamp:
        value = event.get("session_day") or event.get("_source_day")
        try:
            return dt.date.fromisoformat(str(value)[:10]) if value else None
        except Exception:
            return None

    source = str(event.get("tournament_source_name") or event.get("tournament_name") or "")
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


def _session_window(day: dt.date, timezone: str, cutoff: int) -> tuple[dt.datetime, dt.datetime]:
    tz = ZoneInfo(timezone)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz) + dt.timedelta(minutes=cutoff)
    return start, start + dt.timedelta(days=1)


def install_webhook(module: Any) -> None:
    global _WEBHOOK_INSTALLED
    if _WEBHOOK_INSTALLED:
        return

    old_session_events = store.session_events
    old_matches_title = module._matches_title
    old_matches_menu = module._matches_menu

    def strict_session_events(old_loader: Any, chat_id: int, day: dt.date, force_refresh: bool = False) -> List[Dict[str, Any]]:
        rows = old_session_events(old_loader, chat_id, day, force_refresh)
        result: List[Dict[str, Any]] = []
        seen: set[int] = set()
        for event in rows:
            try:
                event_id = int(event.get("event_id") or 0)
            except Exception:
                continue
            if not event_id or event_id in seen:
                continue
            game_day = _event_game_day(event, day)
            if game_day != day:
                print(
                    "[session-safety] drop outside game day "
                    f"selected={day} actual={game_day} event_id={event_id} "
                    f"{event.get('home_name')} - {event.get('away_name')}"
                )
                continue
            seen.add(event_id)
            event["session_day"] = day.isoformat()
            result.append(event)
        return result

    store.session_events = strict_session_events
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    def matches_title(chat_id: int, group: str, tournament: str, day: dt.date) -> str:
        matches = module._matches_for_state(chat_id, group, tournament, day)
        timezone = store.DEFAULT_TZ
        cutoff = store.DEFAULT_CUTOFF
        if matches:
            timezone = str(matches[0].get("tournament_timezone") or timezone)
            try:
                cutoff = int(matches[0].get("tournament_cutoff_minutes", cutoff))
            except Exception:
                cutoff = store.DEFAULT_CUTOFF
        else:
            profile = store.get_profile(tournament)
            timezone = str(profile.get("tz") or timezone)
            cutoff = int(profile.get("cutoff", cutoff))

        try:
            start, end = _session_window(day, timezone, cutoff)
            window = f"{start:%d.%m %H:%M} → {end:%d.%m %H:%M} · {timezone}"
        except Exception:
            window = timezone

        groups = store.stage_groups(module, chat_id, group, tournament, day)
        stage_filter = ""
        try:
            state, payload = module.get_state(chat_id)
            payload = payload or {}
            if state == "picked_tournament":
                stage_filter = str(payload.get("stage_filter") or "")
        except Exception:
            pass
        tail = f"Стадия: {stage_filter}" if stage_filter else ("Выбери стадию:" if len(groups) > 1 else "Выбери матчи:")
        return (
            f"{ss.tour_label(group)} · {tournament}\n"
            f"📅 МАТЧИ СЕГОДНЯ · {len(matches)}\n"
            f"Игровой день: {day:%d.%m.%Y}\n"
            f"Окно турнира: {window}\n"
            f"{tail}"
        )

    def matches_menu(chat_id: int, group: str, tournament: str, day=None):
        day = day or module._active_day(chat_id)
        markup = old_matches_menu(chat_id, group, tournament, day)
        rows = list((markup or {}).get("inline_keyboard", []))
        total = len(module._matches_for_state(chat_id, group, tournament, day))
        header = [module._btn(f"📅 МАТЧИ СЕГОДНЯ · {total}", "noop")]
        # Keep a single visible heading directly above 1/64, 1/32, etc.
        if not rows or not rows[0] or str(rows[0][0].get("text") or "") != header[0]["text"]:
            rows.insert(0, header)
        return module._kb(rows)

    module._matches_title = matches_title
    module._matches_menu = matches_menu
    _WEBHOOK_INSTALLED = True
