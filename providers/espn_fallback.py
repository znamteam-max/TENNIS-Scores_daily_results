from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
from typing import Any, Dict, List, Optional

import httpx


ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
LEAGUES = ("atp", "wta")


def _stable_id(raw_id: Any) -> int:
    digest = hashlib.md5(f"espn:{raw_id or ''}".encode("utf-8")).hexdigest()
    return 900_000_000 + (int(digest[:9], 16) % 90_000_000)


def _parse_timestamp(value: Any) -> Optional[int]:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(dt.datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


async def _fetch_json(client: httpx.AsyncClient, league: str, day: dt.date) -> Dict[str, Any]:
    url = f"{ESPN_BASE}/{league}/scoreboard"
    params = {"dates": day.strftime("%Y%m%d")}
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _status(comp: Dict[str, Any]) -> Dict[str, str]:
    status_type = ((comp.get("status") or {}).get("type") or {})
    state = str(status_type.get("state") or "").lower()
    completed = bool(status_type.get("completed"))
    detail = str(status_type.get("detail") or status_type.get("description") or "")
    lowered = detail.lower()
    if "retir" in lowered:
        kind = "retired"
    elif "walkover" in lowered or "w/o" in lowered:
        kind = "walkover"
    elif any(token in lowered for token in ("interrupt", "abandon", "suspend", "прерван")):
        kind = "interrupted"
    elif "cancel" in lowered:
        kind = "cancelled"
    elif completed or state == "post":
        kind = "finished"
    elif state == "in":
        kind = "inprogress"
    else:
        kind = "notstarted"
    return {"type": kind, "detail": detail}


def _side(comp: Dict[str, Any], side: str) -> Dict[str, Any]:
    wanted = side == "home"
    for row in comp.get("competitors") or []:
        if str(row.get("homeAway") or "").lower() == side:
            return row
    competitors = comp.get("competitors") or []
    if len(competitors) >= 2:
        return competitors[0 if wanted else 1]
    return {}


def _score(home: Dict[str, Any], away: Dict[str, Any], side: str) -> Dict[str, Any]:
    current = 0
    rows = home.get("linescores") if side == "home" else away.get("linescores")
    other_rows = away.get("linescores") if side == "home" else home.get("linescores")
    rows = rows or []
    other_rows = other_rows or []
    out: Dict[str, Any] = {}
    for idx, row in enumerate(rows[:5], start=1):
        value = row.get("value")
        if isinstance(value, (int, float)):
            value = int(value)
        out[f"period{idx}"] = value
        tie = row.get("tiebreak")
        if tie not in (None, ""):
            out[f"period{idx}TieBreak"] = int(tie)
        try:
            other_value = other_rows[idx - 1].get("value")
            if float(value) > float(other_value):
                current += 1
        except Exception:
            pass
    out["current"] = current
    out["display"] = current
    return out


def _event(event: Dict[str, Any], grouping: Dict[str, Any], comp: Dict[str, Any], league: str) -> Optional[Dict[str, Any]]:
    home = _side(comp, "home")
    away = _side(comp, "away")
    home_name = ((home.get("athlete") or {}).get("displayName") or home.get("displayName") or "").strip()
    away_name = ((away.get("athlete") or {}).get("displayName") or away.get("displayName") or "").strip()
    if not home_name or not away_name:
        return None
    comp_id = comp.get("id") or comp.get("uid")
    category = "WTA" if league == "wta" else "ATP"
    raw: Dict[str, Any] = {
        "id": _stable_id(f"{league}:{comp_id}"),
        "customId": f"espn:{league}:{comp_id}",
        "tournament": {
            "name": event.get("name") or event.get("shortName") or "Tournament",
            "uniqueTournament": {
                "name": event.get("name") or event.get("shortName") or "Tournament",
                "category": {"name": category, "slug": category.lower()},
            },
            "category": {"name": category, "slug": category.lower()},
        },
        "season": {"name": event.get("name") or event.get("shortName") or ""},
        "homeTeam": {"name": home_name},
        "awayTeam": {"name": away_name},
        "startTimestamp": _parse_timestamp(comp.get("date") or comp.get("startDate")),
        "status": _status(comp),
        "homeScore": _score(home, away, "home"),
        "awayScore": _score(home, away, "away"),
        "source": "espn",
        "espn_league": league,
        "espn_grouping": ((grouping.get("grouping") or {}).get("displayName") or ""),
    }
    if bool(home.get("winner")):
        raw["winnerCode"] = 1
    elif bool(away.get("winner")):
        raw["winnerCode"] = 2
    return raw


def _events_from_scoreboard(data: Dict[str, Any], league: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in data.get("events") or []:
        for grouping in event.get("groupings") or []:
            for comp in grouping.get("competitions") or []:
                raw = _event(event, grouping, comp, league)
                if raw:
                    out.append(raw)
    return out


async def events_by_date(day: dt.date) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        results = await asyncio.gather(*(_fetch_json(client, league, day) for league in LEAGUES), return_exceptions=True)
    events: List[Dict[str, Any]] = []
    for league, result in zip(LEAGUES, results):
        if isinstance(result, Exception):
            print(f"[WARN] espn fallback fetch failed for {day} league={league}: {result}")
            continue
        events.extend(_events_from_scoreboard(result, league))
    return {"source": "espn", "events": events}
