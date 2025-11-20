from __future__ import annotations

import datetime as dt, random, asyncio
from typing import Dict, Any, List, Optional
import httpx

BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
]

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofasscore.com".replace("ss", "s"),  # мелкий трюк
    "Connection": "keep-alive",
}

def _ds(d: dt.date) -> str:
    return d.isoformat()

async def _fetch_json(url: str) -> Optional[Dict[str, Any]]:
    headers = dict(HEADERS_BASE)
    headers["User-Agent"] = random.choice(UAS)
    async with httpx.AsyncClient(http2=False, timeout=20.0) as c:
        r = await c.get(url, headers=headers, follow_redirects=True)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return None

async def events_by_date(d: dt.date) -> Dict[str, Any]:
    """Возвращает структуру вида {"events":[...]} или {}. Никогда не бросает наружу."""
    paths = [
        f"/sport/tennis/scheduled-events/{_ds(d)}",
        f"/sport/tennis/events/{_ds(d)}",
    ]
    for base in BASES:
        for path in paths:
            data = await _fetch_json(f"{base}{path}")
            if data and isinstance(data, dict):
                return data
            await asyncio.sleep(0.7)
    # запасной «live», чтобы хоть что-то показать
    live = await _fetch_json(f"{BASES[0]}/sport/tennis/events/live")
    return live or {}

def classify(ev: Dict[str, Any]) -> str:
    """Грубая классификация: ATP / Challengers / Другие."""
    # Пытаемся по имени uniqueTournament/category
    t = (ev or {}).get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = (ut.get("category") or t.get("category") or {})
    cname = (cat.get("name") or "").lower()
    uname = (ut.get("name") or t.get("name") or "").lower()
    if "challenger" in uname or "challenger" in cname:
        return "Challengers"
    if "atp" in cname and "challenger" not in cname:
        return "ATP"
    # Можно расширять: wta/itf/utr/davis cup и т.п.
    return "Другие"
