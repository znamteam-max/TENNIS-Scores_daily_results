# providers/sofascore.py
from __future__ import annotations
import asyncio, time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Tuple

import httpx

# максимально «браузерные» заголовки — помогают пройти 403
DEFAULT_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "accept": "application/json, text/plain, */*",
    "accept-language": "ru,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "origin": "https://www.sofascore.com",
    "referer": "https://www.sofascore.com/",
}

BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    r = await client.get(url, headers=DEFAULT_HEADERS, timeout=20.0, follow_redirects=True)
    r.raise_for_status()
    return r.json()

def _ds(d: date) -> str:
    return d.isoformat()

def event_id_of(ev: Dict[str, Any]) -> str:
    # универсальный способ достать id
    eid = ev.get("id") or ev.get("event", {}).get("id")
    return str(eid)

def _is_bad_category(ev: Dict[str, Any]) -> bool:
    # отфильтровываем 15/25/50
    cat = ((ev.get("tournament") or {}).get("category") or {}).get("id")
    return cat in (15, 25, 50)

def _tour_label(ev: Dict[str, Any]) -> Tuple[str, str]:
    t = ev.get("tournament") or {}
    cat = t.get("category") or {}
    # Примеры: "ATP - ОДИНОЧНЫЙ РАЗРЯД: Итоговый турнир - Турин"
    cat_name = cat.get("name", "") or ""
    tour_name = t.get("name", "") or ""
    return (f"{cat_name} — {tour_name}", f"{cat.get('uniqueId','')}/{t.get('uniqueId','')}")

def group_tournaments(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if _is_bad_category(ev):
            continue
        label, tid = _tour_label(ev)
        b = buckets.setdefault(tid, {"id": tid, "name": label, "events": []})
        b["events"].append(ev)
    return list(buckets.values())

def _from_ts(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)

def event_status(ev: Dict[str, Any]) -> Dict[str, Any]:
    st = (ev.get("status") or {}).get("type")
    start_ts = ev.get("startTimestamp")
    start_dt = _from_ts(start_ts) if start_ts else None
    home = (ev.get("homeTeam") or {}).get("name", "")
    away = (ev.get("awayTeam") or {}).get("name", "")
    score = ev.get("homeScore") or {}
    ascore = ev.get("awayScore") or {}
    # У Sofascore для тенниса set1/set2... + current
    res = {
        "state": st,              # NOT_STARTED / INPROGRESS / FINISHED / POSTPONED ...
        "start": start_dt,
        "home": home,
        "away": away,
        "score": {"home": score, "away": ascore},
    }
    return res

async def _try_get(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    last_exc = None
    for base in BASES:
        try:
            return await _get_json(client, f"{base}{path}")
        except Exception as e:
            last_exc = e
            await asyncio.sleep(0.3)
    if last_exc:
        raise last_exc
    raise RuntimeError("No sources")

async def events_by_date(client: httpx.AsyncClient, d: date) -> List[Dict[str, Any]]:
    # scheduled на день
    data = await _try_get(client, f"/sport/tennis/scheduled-events/{_ds(d)}")
    return data.get("events") or []

async def events_live(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    data = await _try_get(client, "/sport/tennis/events/live")
    return data.get("events") or []

async def event_details(client: httpx.AsyncClient, event_id: str) -> Dict[str, Any]:
    # на будущее (статистика после матча)
    data = await _try_get(client, f"/event/{event_id}")
    return data
