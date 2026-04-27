from __future__ import annotations

import asyncio
import datetime as dt
import random
from typing import Any, Dict, List, Optional

import httpx

TOUR_LABELS = {
    "men": "Мужской тур",
    "women": "Женский тур",
}

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


def tour_group(ev: Dict[str, Any]) -> str:
    category = classify(ev)
    cname = _category_name(ev)
    tname = _lower(_tournament_name(ev), _season_name(ev))
    hay = f"{cname} {tname}"

    if category == "WTA" or "wta" in hay or "women" in hay or "female" in hay:
        return "women"

    if any(x in hay for x in ["w15", "w25", "w35", "w50", "w75", "w100"]):
        return "women"

    if category in {"ATP", "Challenger"} or "atp" in hay or "challenger" in hay or "men" in hay or "male" in hay:
        return "men"

    if any(x in hay for x in ["m15", "m25", "m35", "m50"]):
        return "men"

    return "other"


def tour_label(group: str) -> str:
    return TOUR_LABELS.get(group, "Другой тур")


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
    group = tour_group(ev)

    return {
        "event_id": int(ev.get("id")),
        "custom_id": ev.get("customId"),
        "tournament_name": tournament_name,
        "season_name": _season_name(ev),
        "category": category_name,
        "tour_group": group,
        "tour_label": tour_label(group),
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


def filter_by_tour_group(events: List[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    group = (group or "").strip().lower()
    if not group:
        return events
    return [e for e in events if e.get("tour_group") == group]


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


def tournaments_for_tour_group(events: List[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    rows = filter_by_tour_group(events, group)
    bucket: Dict[str, Dict[str, Any]] = {}

    for e in rows:
        key = e["tournament_name"]
        if key not in bucket:
            bucket[key] = {
                "tournament_name": e["tournament_name"],
                "tour_group": e["tour_group"],
                "tour_label": e["tour_label"],
                "matches_count": 0,
            }
        bucket[key]["matches_count"] += 1

    return sorted(bucket.values(), key=lambda x: (x["tournament_name"].lower(), x["matches_count"]))


def matches_for_tournament(events: List[Dict[str, Any]], category: str, tournament_name: str) -> List[Dict[str, Any]]:
    rows = filter_by_category(events, category)
    rows = [e for e in rows if e["tournament_name"] == tournament_name]
    rows.sort(key=lambda x: (x["start_ts"] or 0, x["home_name"], x["away_name"]))
    return rows


def matches_for_tournament_in_tour(events: List[Dict[str, Any]], group: str, tournament_name: str) -> List[Dict[str, Any]]:
    rows = filter_by_tour_group(events, group)
    rows = [e for e in rows if e["tournament_name"] == tournament_name]
    rows.sort(key=lambda x: (x["start_ts"] or 0, x["home_name"], x["away_name"]))
    return rows


def is_finished(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    status = (event.get("status_type") or ((raw.get("status") or {}).get("type") or "")).lower()
    return status == "finished"


def _score_obj(event: Dict[str, Any], side: str) -> Dict[str, Any]:
    raw = event.get("raw") or {}
    key = "homeScore" if side == "home" else "awayScore"
    obj = raw.get(key) or {}
    return obj if isinstance(obj, dict) else {}


def _score_value(score: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = score.get(key)
        if value is not None:
            return value
    return None


def _fmt_score_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def final_score(event: Dict[str, Any]) -> str:
    home = _score_obj(event, "home")
    away = _score_obj(event, "away")
    home_total = _score_value(home, "current", "display")
    away_total = _score_value(away, "current", "display")
    if home_total is None or away_total is None:
        return ""
    return f"{_fmt_score_value(home_total)}-{_fmt_score_value(away_total)}"


def set_scores(event: Dict[str, Any]) -> List[str]:
    home = _score_obj(event, "home")
    away = _score_obj(event, "away")
    sets: List[str] = []

    for idx in range(1, 6):
        home_games = _score_value(home, f"period{idx}")
        away_games = _score_value(away, f"period{idx}")
        if home_games is None or away_games is None:
            continue

        item = f"{_fmt_score_value(home_games)}-{_fmt_score_value(away_games)}"
        home_tb = _score_value(home, f"period{idx}TieBreak")
        away_tb = _score_value(away, f"period{idx}TieBreak")
        if home_tb not in (None, 0, "0") or away_tb not in (None, 0, "0"):
            item += f" ({_fmt_score_value(home_tb or 0)}-{_fmt_score_value(away_tb or 0)})"
        sets.append(item)

    return sets


def winner_name(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    winner_code = raw.get("winnerCode")
    if str(winner_code) == "1":
        return str(event.get("home_name") or "")
    if str(winner_code) == "2":
        return str(event.get("away_name") or "")
    return ""


def result_message(event: Dict[str, Any]) -> str:
    lines = [
        "Матч завершен",
        f"{event.get('tour_label') or tour_label(event.get('tour_group', ''))}: {event.get('tournament_name') or 'Турнир'}",
        "",
        f"{event.get('home_name') or 'TBD'} - {event.get('away_name') or 'TBD'}",
    ]

    total = final_score(event)
    if total:
        lines.append(f"Итог: {total}")

    sets = set_scores(event)
    if sets:
        lines.append(f"По сетам: {', '.join(sets)}")

    winner = winner_name(event)
    if winner:
        lines.append(f"Победитель: {winner}")

    return "\n".join(lines).strip()
