from __future__ import annotations

import copy
import datetime as dt
import hashlib
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import db_pg
from providers import sofascore as ss

DEFAULT_TZ = os.getenv("DEFAULT_TOURNAMENT_TZ", "Europe/Helsinki")
DEFAULT_CUTOFF = int(os.getenv("TOURNAMENT_DAY_CUTOFF_MINUTES", "360"))
STAGE_ORDER = {
    "Квалификация": 1,
    "1/64 финала": 10,
    "1/32 финала": 20,
    "1/16 финала": 30,
    "1/8 финала": 40,
    "1/4 финала": 50,
    "1/2 финала": 60,
    "Финал": 70,
    "Стадия не определена": 99,
}
TZ_HINTS = (
    (("toronto", "торонто", "montreal", "монреаль", "canada", "канада"), "America/Toronto"),
    (("cincinnati", "цинциннати", "washington", "вашингтон", "new york", "нью-йорк", "us open"), "America/New_York"),
    (("indian wells", "индиан-уэллс", "los angeles", "лос-анджелес"), "America/Los_Angeles"),
    (("shanghai", "шанхай", "beijing", "пекин", "wuhan", "ухань", "ningbo", "нинбо"), "Asia/Shanghai"),
    (("tokyo", "токио"), "Asia/Tokyo"),
    (("seoul", "сеул"), "Asia/Seoul"),
    (("dubai", "дубай", "abu dhabi", "абу-даби"), "Asia/Dubai"),
    (("doha", "доха"), "Asia/Qatar"),
    (("australian open", "австрали", "melbourne", "мельбурн"), "Australia/Sydney"),
    (("wimbledon", "уимблдон", "london", "лондон", "eastbourne", "истборн"), "Europe/London"),
    (("roland garros", "french open", "ролан гаррос", "paris", "париж"), "Europe/Paris"),
)

_CACHE: Dict[tuple[int, str], tuple[float, List[Dict[str, Any]]]] = {}
_PROFILES: tuple[float, Dict[str, Dict[str, Any]]] = (0.0, {})
_STAGE_OVERRIDES: Dict[int, str] = {}
WATCH_CONTEXT: Dict[int, Dict[str, Any]] = {}
_SCHEMA_READY = False
_ROUND_CAPTURED = False


def norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def profile_key(name: str) -> str:
    raw = re.sub(r"[^a-zа-яё0-9]+", " ", norm(name)).strip()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def guess_timezone(name: str) -> str:
    hay = norm(name)
    for needles, timezone in TZ_HINTS:
        if any(needle in hay for needle in needles):
            return timezone
    return DEFAULT_TZ


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            create table if not exists tournament_profiles (
                profile_key text primary key,
                source_name text not null,
                display_name text not null,
                timezone text not null,
                cutoff_minutes integer not null default 360,
                updated_at timestamptz not null default now()
            );
            create table if not exists match_stage_overrides (
                event_id bigint primary key,
                stage text not null,
                updated_at timestamptz not null default now()
            );
            alter table match_watches add column if not exists source_day date;
            alter table match_watches add column if not exists source_tournament_name text;
            alter table match_watches add column if not exists stage text;
            """
        )
    _SCHEMA_READY = True


def profiles() -> Dict[str, Dict[str, Any]]:
    global _PROFILES
    if time.time() - _PROFILES[0] < 60:
        return _PROFILES[1]
    ensure_schema()
    data: Dict[str, Dict[str, Any]] = {}
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("select profile_key, source_name, display_name, timezone, cutoff_minutes from tournament_profiles")
        for key, source, name, timezone, cutoff in cur.fetchall():
            data[str(key)] = {
                "key": str(key),
                "source": str(source),
                "name": str(name),
                "tz": str(timezone),
                "cutoff": int(cutoff),
            }
    _PROFILES = (time.time(), data)
    return data


def get_profile(source: str) -> Dict[str, Any]:
    key = profile_key(source)
    return dict(
        profiles().get(key)
        or {
            "key": key,
            "source": source,
            "name": source,
            "tz": guess_timezone(source),
            "cutoff": DEFAULT_CUTOFF,
        }
    )


def save_profile(source: str, **changes: Any) -> Dict[str, Any]:
    global _PROFILES
    value = get_profile(source)
    value.update({key: item for key, item in changes.items() if item is not None})
    value["name"] = " ".join(str(value["name"]).split())
    ZoneInfo(str(value["tz"]))
    value["cutoff"] = int(value["cutoff"])
    if not value["name"] or not 0 <= value["cutoff"] < 1440:
        raise ValueError("Некорректные настройки")
    ensure_schema()
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            insert into tournament_profiles (
                profile_key, source_name, display_name, timezone, cutoff_minutes, updated_at
            ) values (%s, %s, %s, %s, %s, now())
            on conflict (profile_key) do update set
                source_name=excluded.source_name,
                display_name=excluded.display_name,
                timezone=excluded.timezone,
                cutoff_minutes=excluded.cutoff_minutes,
                updated_at=now()
            """,
            (value["key"], source, value["name"], value["tz"], value["cutoff"]),
        )
        cur.execute(
            "update match_watches set tournament_name=%s where source_tournament_name=%s",
            (value["name"], source),
        )
    _PROFILES = (0.0, {})
    _CACHE.clear()
    return value


def format_cutoff(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def parse_date(text: str) -> dt.date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("Формат даты: 02.08.2026")


def parse_cutoff(text: str) -> int:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", text.strip())
    if not match:
        raise ValueError("Формат времени: 06:00")
    value = int(match.group(1)) * 60 + int(match.group(2) or 0)
    if value >= 1440:
        raise ValueError("Некорректное время")
    return value


def normalize_stage(value: Any) -> str:
    low = norm(str(value or "").replace("-", " "))
    if not low:
        return ""
    match = re.search(r"(?<!\d)1\s*/\s*(64|32|16|8|4|2)(?!\d)", low)
    if match:
        return f"1/{match.group(1)} финала"
    match = re.search(r"(?:round of|last|\br)\s*(128|64|32|16|8|4|2)\b", low)
    if match:
        size = int(match.group(1))
        return "Финал" if size == 2 else f"1/{size // 2} финала"
    if "quarter" in low or "четвертьфин" in low:
        return "1/4 финала"
    if "semi" in low or "полуфин" in low:
        return "1/2 финала"
    if re.search(r"(^| )final(s)?($| )", low) or low == "финал":
        return "Финал"
    if "qualif" in low or "квалиф" in low:
        return "Квалификация"
    return ""


def stage_overrides(ids: Iterable[int]) -> Dict[int, str]:
    missing = [int(event_id) for event_id in set(ids) if event_id and int(event_id) not in _STAGE_OVERRIDES]
    if missing:
        ensure_schema()
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute("select event_id, stage from match_stage_overrides where event_id=any(%s)", (missing,))
            found = {int(event_id): str(stage) for event_id, stage in cur.fetchall()}
        for event_id in missing:
            _STAGE_OVERRIDES[event_id] = found.get(event_id, "")
    return _STAGE_OVERRIDES


def event_stage(event: Dict[str, Any], overrides: Dict[int, str]) -> str:
    event_id = int(event.get("event_id") or 0)
    if overrides.get(event_id):
        return overrides[event_id]
    raw = event.get("raw") or {}
    status = raw.get("status") or {}
    for value in (
        event.get("stage"),
        raw.get("flashscore_round"),
        raw.get("round"),
        raw.get("stage"),
        status.get("detail"),
        raw.get("flashscore_league"),
    ):
        stage = normalize_stage(value)
        if stage:
            return stage
    return ""


def save_stage(ids: Iterable[int], stage: str) -> str:
    stage = normalize_stage(stage) or " ".join(stage.split())
    if not stage:
        raise ValueError("Пустая стадия")
    ensure_schema()
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        for event_id in set(int(item) for item in ids):
            cur.execute(
                """
                insert into match_stage_overrides (event_id, stage, updated_at)
                values (%s, %s, now())
                on conflict (event_id) do update set stage=excluded.stage, updated_at=now()
                """,
                (event_id, stage),
            )
            cur.execute("update match_watches set stage=%s where event_id=%s", (stage, event_id))
            _STAGE_OVERRIDES[event_id] = stage
    _CACHE.clear()
    return stage


def apply_stages(events: List[Dict[str, Any]]) -> None:
    overrides = stage_overrides(int(event.get("event_id") or 0) for event in events)
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(str(event.get("tour_group")), str(event.get("tournament_name")))].append(event)
    count_map = {
        64: "1/64 финала",
        32: "1/32 финала",
        16: "1/16 финала",
        8: "1/8 финала",
        4: "1/4 финала",
        2: "1/2 финала",
        1: "Финал",
    }
    for rows in grouped.values():
        stages = [event_stage(event, overrides) for event in rows]
        known = [stage for stage in stages if stage]
        fallback = known[0] if known and len(set(known)) == 1 else (count_map.get(len(rows), "") if not known else "")
        for event, stage in zip(rows, stages):
            stage = stage or fallback or "Стадия не определена"
            event["stage"] = stage
            event.setdefault("raw", {})["flashscore_round"] = stage


def session_events(old_loader: Any, chat_id: int, day: dt.date, force_refresh: bool = False) -> List[Dict[str, Any]]:
    cache_key = (int(chat_id), day.isoformat())
    if not force_refresh and cache_key in _CACHE and time.time() - _CACHE[cache_key][0] < 45:
        return copy.deepcopy(_CACHE[cache_key][1])

    by_id: Dict[int, Dict[str, Any]] = {}
    rank = {"finished": 5, "retired": 5, "walkover": 5, "cancelled": 4, "inprogress": 3, "notstarted": 2}
    for source_day in (day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)):
        for row in old_loader(chat_id, source_day, force_refresh=force_refresh):
            event = copy.deepcopy(row)
            event["_source_day"] = source_day.isoformat()
            event_id = int(event["event_id"])
            old = by_id.get(event_id)
            if old is None or rank.get(ss.status_type(event), 1) > rank.get(ss.status_type(old), 1):
                by_id[event_id] = event

    result: List[Dict[str, Any]] = []
    for event in by_id.values():
        source = str(event.get("tournament_name") or "Турнир")
        profile = get_profile(source)
        source_day = dt.date.fromisoformat(event["_source_day"])
        timestamp = int(event.get("start_ts") or 0)
        local_day = source_day
        if timestamp:
            local_day = (
                dt.datetime.fromtimestamp(timestamp, ZoneInfo(profile["tz"]))
                - dt.timedelta(minutes=profile["cutoff"])
            ).date()
        if local_day != day:
            continue
        event.update(
            {
                "tournament_source_name": source,
                "tournament_key": profile["key"],
                "tournament_name": profile["name"],
                "tournament_timezone": profile["tz"],
                "tournament_cutoff_minutes": profile["cutoff"],
                "session_day": day.isoformat(),
            }
        )
        result.append(event)

    apply_stages(result)
    result.sort(
        key=lambda event: (
            int(event.get("tournament_sort_rank", 9)),
            str(event.get("tour_group")),
            str(event.get("tournament_name")).lower(),
            STAGE_ORDER.get(str(event.get("stage")), 90),
            int(event.get("start_ts") or 0),
            int(event["event_id"]),
        )
    )
    _CACHE[cache_key] = (time.time(), copy.deepcopy(result))
    return result


def stage_groups(module: Any, chat_id: int, group: str, tournament: str, day: dt.date):
    bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in module._matches_for_state(chat_id, group, tournament, day):
        bucket[str(event.get("stage") or "Стадия не определена")].append(event)
    return sorted(bucket.items(), key=lambda item: (STAGE_ORDER.get(item[0], 90), item[0]))


def tournament_items(module: Any, chat_id: int, group: str, day: dt.date):
    events = module._load_events_for_chat(chat_id, day)
    items = ss.tournaments_for_tour_group(events, group)
    for item in items:
        rows = [
            event
            for event in events
            if event.get("tour_group") == group and event.get("tournament_name") == item.get("tournament_name")
        ]
        if not rows:
            continue
        counts: Dict[str, int] = defaultdict(int)
        for event in rows:
            counts[str(event.get("stage") or "Стадия не определена")] += 1
        first = rows[0]
        item.update(
            {
                "source_name": first.get("tournament_source_name"),
                "timezone": first.get("tournament_timezone"),
                "cutoff_minutes": first.get("tournament_cutoff_minutes"),
                "stage_counts": sorted(counts.items(), key=lambda pair: (STAGE_ORDER.get(pair[0], 90), pair[0])),
            }
        )
    return items


def save_watch_metadata(chat_id: int, day: dt.date, match: Dict[str, Any]) -> None:
    ensure_schema()
    source_day = dt.date.fromisoformat(str(match.get("_source_day") or day.isoformat()))
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            update match_watches set
                source_day=%s,
                source_tournament_name=%s,
                tournament_name=%s,
                stage=%s
            where chat_id=%s and day=%s and event_id=%s
            """,
            (
                source_day,
                match.get("tournament_source_name"),
                match.get("tournament_name"),
                match.get("stage"),
                chat_id,
                day,
                int(match["event_id"]),
            ),
        )


def pending_source_days() -> List[dt.date]:
    ensure_schema()
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            select distinct coalesce(source_day, day)
            from match_watches
            where notified_at is null
              and day >= current_date - interval '4 days'
            order by 1
            """
        )
        return [row[0] for row in cur.fetchall()]


def pending_watches(source_day: dt.date) -> List[Dict[str, Any]]:
    ensure_schema()
    result: List[Dict[str, Any]] = []
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            select chat_id, day, event_id, category, tournament_name,
                   home_name, away_name, start_ts, source_tournament_name, stage
            from match_watches
            where coalesce(source_day, day)=%s and notified_at is null
            """,
            (source_day,),
        )
        for row in cur.fetchall():
            item = {
                "chat_id": row[0],
                "session_day": row[1],
                "event_id": row[2],
                "category": row[3],
                "tournament_name": row[4],
                "home_name": row[5],
                "away_name": row[6],
                "start_ts": row[7],
                "source_tournament_name": row[8],
                "stage": row[9],
            }
            result.append(item)
            WATCH_CONTEXT[int(row[2])] = item
    return result


def mark_notified(chat_id: Optional[int], source_day: dt.date, event_id: int) -> bool:
    ensure_schema()
    where = "event_id=%s and coalesce(source_day, day)=%s and notified_at is null"
    args: List[Any] = [event_id, source_day]
    if chat_id is not None:
        where = "chat_id=%s and " + where
        args.insert(0, chat_id)
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(f"update match_watches set notified_at=now() where {where}", args)
        return cur.rowcount > 0


def install_round_capture() -> None:
    global _ROUND_CAPTURED
    if _ROUND_CAPTURED or not hasattr(ss, "_flashscore_event"):
        return
    old = ss._flashscore_event

    def wrapped(fields, league):
        event = old(fields, league)
        if event:
            for value in [*fields.values(), *league.values()]:
                stage = normalize_stage(value)
                if stage:
                    event["flashscore_round"] = stage
                    break
        return event

    ss._flashscore_event = wrapped
    _ROUND_CAPTURED = True
