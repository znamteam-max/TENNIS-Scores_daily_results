from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import tournament_session_store as store
from providers import sofascore as ss


_INSTALLED = False


def _event(
    *,
    event_id: int,
    day: dt.date,
    group: str,
    home: str,
    away: str,
    home_total: int,
    away_total: int,
    home_sets: list[int],
    away_sets: list[int],
    start_hour_utc: int,
) -> dict[str, Any]:
    category = "ATP" if group == "men" else "WTA"
    start_ts = int(
        dt.datetime(
            day.year,
            day.month,
            day.day,
            start_hour_utc,
            0,
            tzinfo=dt.timezone.utc,
        ).timestamp()
    )
    home_score: dict[str, Any] = {"current": home_total, "display": home_total}
    away_score: dict[str, Any] = {"current": away_total, "display": away_total}
    for idx, value in enumerate(home_sets, start=1):
        home_score[f"period{idx}"] = value
    for idx, value in enumerate(away_sets, start=1):
        away_score[f"period{idx}"] = value

    raw = {
        "id": event_id,
        "customId": f"known-us-open-2026-{group}-final",
        "source": "known_history_backfill",
        "tournament": {
            "name": "US Open",
            "uniqueTournament": {
                "name": "US Open",
                "category": {"name": category, "slug": category.lower()},
            },
            "category": {"name": category, "slug": category.lower()},
        },
        "season": {"name": "US Open 2026"},
        "homeCompetitor": {"name": home, "shortName": home},
        "awayCompetitor": {"name": away, "shortName": away},
        "startTimestamp": start_ts,
        "status": {"type": "finished", "description": "Finished"},
        "homeScore": home_score,
        "awayScore": away_score,
        "winnerCode": 1,
        "flashscore_round": "Финал",
    }
    return {
        "event_id": event_id,
        "custom_id": raw["customId"],
        "tournament_name": "US Open",
        "season_name": "US Open 2026",
        "category": category,
        "tournament_status": "Grand Slam",
        "tournament_sort_rank": 0,
        "tour_group": group,
        "tour_label": ss.tour_label(group),
        "home_name": home,
        "away_name": away,
        "start_ts": start_ts,
        "status_type": "finished",
        "stage": "Финал",
        "session_day": day.isoformat(),
        "_source_day": day.isoformat(),
        "raw": raw,
    }


def _known(day: dt.date) -> list[dict[str, Any]]:
    if day == dt.date(2026, 9, 13):
        # Alexander Zverev d. Ben Shelton 6-3, 7-6(2), 5-7, 6-2.
        return [
            _event(
                event_id=996_091_301,
                day=day,
                group="men",
                home="Alexander Zverev",
                away="Ben Shelton",
                home_total=3,
                away_total=1,
                home_sets=[6, 7, 5, 6],
                away_sets=[3, 6, 7, 2],
                start_hour_utc=18,
            )
        ]
    if day == dt.date(2026, 9, 12):
        # Elena Rybakina d. Aryna Sabalenka 6-4, 5-7, 6-2.
        return [
            _event(
                event_id=996_091_201,
                day=day,
                group="women",
                home="Elena Rybakina",
                away="Aryna Sabalenka",
                home_total=2,
                away_total=1,
                home_sets=[6, 5, 6],
                away_sets=[4, 7, 2],
                start_hour_utc=20,
            )
        ]
    return []


def _surname(value: Any) -> str:
    text = " ".join(str(value or "").lower().replace("ё", "е").split())
    return text.split()[-1] if text else ""


def _same_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if str(left.get("tour_group") or "") != str(right.get("tour_group") or ""):
        return False
    left_names = {_surname(left.get("home_name")), _surname(left.get("away_name"))}
    right_names = {_surname(right.get("home_name")), _surname(right.get("away_name"))}
    return bool(left_names and left_names == right_names)


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    old_load = module._load_events_for_chat

    def load_events(chat_id: int, day=None, force_refresh: bool = False):
        day = day or module._active_day(chat_id)
        rows = list(old_load(chat_id, day, force_refresh=force_refresh))
        known = _known(day)
        if not known:
            return rows

        added = 0
        for event in known:
            if any(_same_match(event, current) for current in rows):
                continue
            row = copy.deepcopy(event)
            profile = store.get_profile(str(row.get("tournament_name") or "US Open"))
            row.update(
                {
                    "tournament_source_name": row.get("tournament_name") or "US Open",
                    "tournament_key": profile["key"],
                    "tournament_timezone": profile["tz"],
                    "tournament_cutoff_minutes": profile["cutoff"],
                }
            )
            rows.append(row)
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
            print(f"[history] applied known major backfill day={day} added={added}")
        return rows

    module._load_events_for_chat = load_events
    _INSTALLED = True
