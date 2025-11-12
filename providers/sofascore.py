import httpx, asyncio, re
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple
from unicodedata import normalize as uni_norm
from config import HTTP_TIMEOUT

BASE = "https://api.sofascore.com/api/v1"

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", uni_norm("NFKC", s or "").strip().lower())

async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    r = await client.get(url, timeout=HTTP_TIMEOUT, headers={
        "User-Agent": "Mozilla/5.0 TennisBot/1.0 (+https://example.com)"
    })
    r.raise_for_status()
    return r.json()

async def events_by_date(client: httpx.AsyncClient, day: date) -> List[Dict[str, Any]]:
    ds = day.isoformat()
    data = await _get_json(client, f"{BASE}/sport/tennis/scheduled-events/{ds}")
    return data.get("events", [])

def _match_player_in_event(event: Dict[str, Any], queries: List[str]) -> bool:
    hn = _norm(event.get("homeTeam", {}).get("name", ""))
    an = _norm(event.get("awayTeam", {}).get("name", ""))
    for q in queries:
        qn = _norm(q)
        if qn and (qn in hn or qn in an):
            return True
    return False

def _extract_sets(event: Dict[str, Any]) -> List[str]:
    # SofaScore tennis 'homeScore'/'awayScore' often have 'period1', 'period2', ... etc.
    sets = []
    hs = event.get("homeScore", {}) or {}
    as_ = event.get("awayScore", {}) or {}
    for i in range(1, 6):  # up to 5 sets
        h = hs.get(f"period{i}")
        a = as_.get(f"period{i}")
        if h is None or a is None:
            continue
        sets.append(f"{h}:{a}")
    return sets or ([f"{hs.get('current', '?')}:{as_.get('current','?')}"] if hs.get('current') is not None and as_.get('current') is not None else [])

async def event_statistics(client: httpx.AsyncClient, event_id: int) -> Dict[str, Any]:
    # Returns normalized structure for formatter
    details = await _get_json(client, f"{BASE}/event/{event_id}")
    event = details.get("event") or {}
    sets = _extract_sets(event)

    # duration often missing; leave None if not present
    duration = None
    try:
        inc = await _get_json(client, f"{BASE}/event/{event_id}/incidents")
        # sometimes 'length' is inside 'event' or incidents meta
        # if present in minutes, convert to H:MM
        length = (inc.get("length") or details.get("event", {}).get("length"))
        if isinstance(length, int) and length > 0:
            h = length // 60
            m = length % 60
            duration = f"{h}:{m:02d}" if h else f"{m} мин"
    except httpx.HTTPError:
        pass

    stats = {}
    try:
        st = await _get_json(client, f"{BASE}/event/{event_id}/statistics")
        # structure: {'statistics': [{'period': 'ALL', 'groups': [{'name':'Serve','statisticsItems':[...]}]}]}
        for root in st.get("statistics", []):
            if root.get("period") != "ALL":
                continue
            for g in root.get("groups", []):
                for item in g.get("statisticsItems", []):
                    name = item.get("name", "")
                    h = item.get("home", None)
                    a = item.get("away", None)
                    # map by known names
                    key = None
                    name_l = name.lower()
                    if "ace" in name_l:
                        key = "aces"
                    elif "double" in name_l:
                        key = "doubles"
                    elif "first serve in" in name_l or "1st serve in" in name_l:
                        key = "first_serve_in_pct"
                    elif "first serve points won" in name_l or "1st serve points won" in name_l:
                        key = "first_serve_points_won_pct"
                    elif "second serve points won" in name_l or "2nd serve points won" in name_l:
                        key = "second_serve_points_won_pct"
                    elif "winners" in name_l:
                        key = "winners"
                    elif "unforced errors" in name_l:
                        key = "unforced"
                    elif "break points saved" in name_l:
                        key = "break_points_saved"
                    elif "break points faced" in name_l:
                        key = "break_points_faced"
                    elif "match points saved" in name_l:
                        key = "match_points_saved"
                    else:
                        continue

                    if key not in stats:
                        stats[key] = {"home": None, "away": None}
                    stats[key]["home"] = h
                    stats[key]["away"] = a
    except httpx.HTTPError:
        pass

    def pack_side(side: str) -> dict:
        def get_num(k):
            v = stats.get(k, {}).get(side)
            return v
        def get_pct(k):
            v = stats.get(k, {}).get(side)
            # if SofaScore returns "62" or 62 or "62%"
            if v is None:
                return None
            try:
                if isinstance(v, str) and v.endswith('%'):
                    v = v[:-1]
                return float(v)
            except Exception:
                return None
        return {
            "aces": get_num("aces"),
            "doubles": get_num("doubles"),
            "first_serve_in_pct": get_pct("first_serve_in_pct"),
            "first_serve_points_won_pct": get_pct("first_serve_points_won_pct"),
            "second_serve_points_won_pct": get_pct("second_serve_points_won_pct"),
            "winners": get_num("winners"),
            "unforced": get_num("unforced"),
            "break_points_saved": get_num("break_points_saved"),
            "break_points_faced": get_num("break_points_faced"),
            "match_points_saved": get_num("match_points_saved"),
        }

    home_name = event.get("homeTeam", {}).get("name", "Игрок A")
    away_name = event.get("awayTeam", {}).get("name", "Игрок B")

    return {
        "event_id": str(event_id),
        "home_name": home_name,
        "away_name": away_name,
        "score_sets": sets,
        "duration": duration,
        "home_stats": pack_side("home"),
        "away_stats": pack_side("away"),
    }

async def find_player_events_today(client: httpx.AsyncClient, day: date, player_queries: List[str]) -> List[Dict[str, Any]]:
    events = await events_by_date(client, day)
    return [e for e in events if _match_player_in_event(e, player_queries)]

def is_finished(event: Dict[str, Any]) -> bool:
    st = (event.get("status", {}) or {}).get("type")
    # SofaScore: status = {"type": "finished"} or {"type":"notstarted","description":"..."}
    return str(st).lower() == "finished"

def event_id_of(event: Dict[str, Any]) -> Optional[str]:
    eid = event.get("id")
    return str(eid) if eid is not None else None
