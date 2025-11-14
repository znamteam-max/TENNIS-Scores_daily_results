# providers/sofascore.py
from __future__ import annotations
import asyncio
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Tuple
import json, httpx

DEFAULT_HEADERS = {
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "accept": "application/json, text/plain, */*",
    "accept-language": "ru,en;q=0.9",
    "origin": "https://www.sofascore.com",
    "referer": "https://www.sofascore.com/",
    "cache-control": "no-cache",
    "pragma": "no-cache",
}

# основная + прокси-фолбэки (обход 403 challenge)
BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
    # r.jina.ai проксирует контент GET, отдаёт тот же JSON
    "https://r.jina.ai/http://api.sofascore.com/api/v1",
    "https://r.jina.ai/https://api.sofascore.com/api/v1",
]

def _ds(d: date) -> str:
    return d.isoformat()

def _json_from_text(t: str) -> Dict[str, Any]:
    t = t.strip()
    if t.startswith("{") or t.startswith("["):
        return json.loads(t)
    # иногда r.jina.ai может обернуть, но обычно не нужно
    return json.loads(t)

async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    r = await client.get(url, headers=DEFAULT_HEADERS, timeout=25.0, follow_redirects=True)
    # если это прокси — content-type может быть text/plain
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            return _json_from_text(r.text)
    r.raise_for_status()
    return {}

async def _try_get(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for base in BASES:
        try:
            return await _get_json(client, f"{base}{path}")
        except Exception as e:
            last_exc = e
            await asyncio.sleep(0.25)
    if last_exc:
        raise last_exc
    raise RuntimeError("No sources")

def event_id_of(ev: Dict[str, Any]) -> str:
    return str(ev.get("id") or (ev.get("event") or {}).get("id"))

def _is_bad_category(ev: Dict[str, Any]) -> bool:
    cat = ((ev.get("tournament") or {}).get("category") or {}).get("id")
    return cat in (15, 25, 50)

def _tour_label(ev: Dict[str, Any]) -> Tuple[str, str]:
    t = ev.get("tournament") or {}
    cat = t.get("category") or {}
    cat_name = (cat.get("name") or "").strip()
    tour_name = (t.get("name") or "").strip()
    # id для группировки должен быть стабильный
    tid = f"{cat.get('uniqueId','')}/{t.get('uniqueId','')}"
    name = f"{cat_name} — {tour_name}".strip(" —")
    return (name, tid)

def group_tournaments(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if _is_bad_category(ev):
            continue
        name, tid = _tour_label(ev)
        b = buckets.setdefault(tid, {"id": tid, "name": name, "events": []})
        b["events"].append(ev)
    # сортируем для красоты
    return sorted(buckets.values(), key=lambda b: b["name"])

def _from_ts(ts: int | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

def event_status(ev: Dict[str, Any]) -> Dict[str, Any]:
    st = (ev.get("status") or {}).get("type")
    start_dt = _from_ts(ev.get("startTimestamp"))
    home = (ev.get("homeTeam") or {}).get("name", "")
    away = (ev.get("awayTeam") or {}).get("name", "")
    return {"state": st, "start": start_dt, "home": home, "away": away}

async def events_by_date(client: httpx.AsyncClient, d: date) -> List[Dict[str, Any]]:
    data = await _try_get(client, f"/sport/tennis/scheduled-events/{_ds(d)}")
    return data.get("events") or []

async def events_live(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    data = await _try_get(client, "/sport/tennis/events/live")
    return data.get("events") or []

# на будущее — добирание подробностей/статистики
async def event_stats_any(client: httpx.AsyncClient, event_id: str) -> Dict[str, Any]:
    # пробуем несколько вариантов
    paths = [
        f"/event/{event_id}/statistics",
        f"/event/{event_id}",
    ]
    last: Dict[str, Any] = {}
    for p in paths:
        try:
            last = await _try_get(client, p)
            if last:
                break
        except Exception:
            continue
    return last
