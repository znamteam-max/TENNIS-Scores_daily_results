from __future__ import annotations

import asyncio
import datetime as dt
import random
from typing import Any, Dict, List, Optional

import httpx

BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}


def _ds(d: dt.date) -> str:
    return d.isoformat()


async def _fetch_json(url: str) -> Optional[Dict[str, Any]]:
    headers = dict(HEADERS_BASE)
    headers["User-Agent"] = random.choice(UAS)

    async with httpx.AsyncClient(http2=False, timeout=20.0, follow_redirects=True) as c:
        r = await c.get(url, headers=headers)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        try:
            data = r.json()
            return data if isinstance(data, dict) else None
        except Exception:
            return None


async def events_by_date(d: dt.date) -> Dict[str, Any]:
    """
    Возвращает {"events":[...]} или {}.
    """
    paths = [
        f"/sport/tennis/scheduled-events/{_ds(d)}",
        f"/sport/tennis/events/{_ds(d)}",
    ]

    for base in BASES:
        for path in paths:
            data = await _fetch_json(f"{base}{path}")
            if data and isinstance(data, dict) and isinstance(data.get("events"), list):
                return data
            await asyncio.sleep(0.4)

    live = await _fetch_json(f"{BASES[0]}/sport/tennis/events/live")
    if live and isinstance(live.get("events"), list):
        return live

    return {"events": []}


def _lower(*parts: Any) -> str:
    return " ".join(str(p or "").strip().lower() for p in parts if p is not None).strip()


def _category_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = ut.get("category") or t.get("category") or {}
    return _lower(cat.get("name"), cat.get("slug"))


def _tournament_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return (ut.get("name") or t.get("name") or "").strip()


def _season_name(ev: Dict[str, Any]) -> str:
    season = ev.get("season") or {}
    return (season.get("name") or "").strip()


def classify(ev: Dict[str, Any]) -> str:
    """
    Нормальная классификация для меню:
    ATP / WTA / ITF / Challenger / Other
    """
    cname = _category_name(ev)
    tname = _lower(_tournament_name(ev), _season_name(ev))

    hay = f"{cname} {tname}"

    if "itf" in hay or "m15" in hay or "m25" in hay or "m50" in hay or "w15" in hay or "w25" in hay or "w50" in hay or "w75" in hay or "w100" in hay:
        return "ITF"

    if "challenger" in hay:
        return "Challenger"

    if "wta" in hay or "women" in hay or "female" in hay or "billie jean king cup" in hay:
        return "WTA"

    if "atp" in hay or "men" in hay or "male" in hay or "davis cup" in hay or "united cup" in hay:
        return "ATP"

    # эвристика по названию турнира
    if any(x in hay for x in ["roland garros", "wimbledon", "us open", "australian open"]):
        # без пола точно не угадаем, но для меню пусть идет в Other, чтобы не прятать
        return "Other"

    return "Other"


def _side_name(ev: Dict[str, Any], side: str) -> str:
    keys = ["homePlayer", "homeCompetitor", "homeTeam", "home"] if side == "home" else ["awayPlayer", "awayCompetitor", "awayTeam", "away"]
    for k in keys:
        obj = ev.get(k)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("shortName")
            if name:
                return str(name)

    comps = ev.get("competitors")
    if isinstance(comps, list) and len(comps) == 2:
        idx = 0 if side == "home" else 1
        obj = comps[idx] or {}
        name = obj.get("name") or obj.get("shortName")
        if name:
            return str(name)

    return "TBD"


def _start_ts(ev: Dict[str, Any]) -> Optional[int]:
    for k in ("startTimestamp", "startTimeTimestamp"):
        v = ev.get(k)
        if isinstance(v, int):
            return v
    return None


def normalize_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    tournament_name = _tournament_name(ev)
    category_name = classify(ev)

    return {
        "event_id": int(ev.get("id")),
        "custom_id": ev.get("customId"),
        "tournament_name": tournament_name,
        "season_name": _season_name(ev),
        "category": category_name,
        "home_name": _side_name(ev, "home"),
        "away_name": _side_name(ev, "away"),
        "start_ts": _start_ts(ev),
        "status_type": ((ev.get("status") or {}).get("type") or "").lower(),
        "raw": ev,
    }


def normalize_events(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in data.get("events", []) or []:
        try:
            if not ev.get("id"):
                continue
            out.append(normalize_event(ev))
        except Exception:
            continue
    return out


def filter_by_category(events: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    category = (category or "").strip()
    if not category:
        return events
    return [e for e in events if e.get("category") == category]


def tournaments_for_category(events: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    rows = filter_by_category(events, category)
    bucket: Dict[str, Dict[str, Any]] = {}

    for e in rows:
        key = e["tournament_name"]
        if key not in bucket:
            bucket[key] = {
                "tournament_name": e["tournament_name"],
                "category": e["category"],
                "matches_count": 0,
            }
        bucket[key]["matches_count"] += 1

    return sorted(bucket.values(), key=lambda x: (x["tournament_name"].lower(), x["matches_count"]))


def matches_for_tournament(events: List[Dict[str, Any]], category: str, tournament_name: str) -> List[Dict[str, Any]]:
    rows = filter_by_category(events, category)
    rows = [e for e in rows if e["tournament_name"] == tournament_name]
    rows.sort(key=lambda x: (x["start_ts"] or 0, x["home_name"], x["away_name"]))
    return rows
