# providers/sofascore.py
from __future__ import annotations
from datetime import date
from typing import List, Dict, Any
import httpx

DEFAULT_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "accept": "*/*",
    "origin": "https://www.sofascore.com",
    "referer": "https://www.sofascore.com/",
}

API_DOMAINS = [
    "https://api.sofascore.com",
    "https://www.sofascore.com",
]

def _looks_low_category(name: str) -> bool:
    # Отфильтруем ITF 15/25/50 и подобные (простая эвристика)
    n = (name or "").lower()
    return ("itf" in n and any(x in n for x in ("15", "25", "50"))) or any(
        x in n for x in (" w15", " w25", " w50", " m15", " m25", " m50")
    )

async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    r = await client.get(url)
    r.raise_for_status()
    return r.json()

def event_id_of(ev: Dict[str, Any]) -> str:
    # У Sofascore бывает несколько полей, но "id" события — стандартный
    eid = ev.get("id")
    return str(eid) if eid is not None else ""

def _pretty_tour_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    tname = t.get("name") or "Турнир"
    # Отличим пары от одиночки
    evname = (ev.get("name") or "").lower()
    if "double" in evname or "doubles" in evname:
        kind = "ПАРНЫЙ РАЗРЯД"
    else:
        kind = "ОДИНОЧНЫЙ РАЗРЯД"
    # Категория: ATP/WTA/Challenger
    cat = (t.get("category") or {}).get("name") or ""
    cat_up = cat.upper() if cat else ""
    prefix = "ATP" if "ATP" in cat_up else ("WTA" if "WTA" in cat_up else "ЧЕЛЛЕНДЖЕР МУЖЧИНЫ")
    return f"{prefix} - {kind}: {tname}"

def group_tournaments(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        t = ev.get("tournament") or {}
        tid = str(t.get("id") or "")
        tname = t.get("name") or "Турнир"
        if not tid:
            # fallback сгруппировать по имени
            tid = f"name:{tname}"
        if _looks_low_category(tname):
            continue
        key = tid
        if key not in buckets:
            buckets[key] = {"id": tid, "name": _pretty_tour_name(ev), "events": []}
        buckets[key]["events"].append(ev)

    # сортировка: покрупнее названия вверх
    out = list(buckets.values())
    out.sort(key=lambda x: x["name"])
    return out

async def events_by_date(client: httpx.AsyncClient, ds: date) -> List[Dict[str, Any]]:
    ds_str = ds.isoformat()
    last_error = None
    for base in API_DOMAINS:
        try:
            data = await _get_json(client, f"{base}/api/v1/sport/tennis/scheduled-events/{ds_str}")
            arr = data.get("events") or []
            # Лёгкая фильтрация: только матчи (не квали и не отменённые, если поле есть)
            events = []
            for ev in arr:
                t = ev.get("tournament") or {}
                tname = t.get("name") or ""
                if _looks_low_category(tname):
                    continue
                events.append(ev)
            return events
        except Exception as e:
            last_error = e
            continue
    # Если вообще не получилось — прокинем последнюю
    if last_error:
        raise last_error
    return []
