from __future__ import annotations

import datetime as dt
import os
import random
from typing import Any, Dict, Optional

import httpx


SOFASCORE_BASE = (os.getenv("SOFASCORE_BASE") or "https://www.sofascore.com").rstrip("/")

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]


async def _fetch_json(url: str, extra: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
        "Referer": f"{SOFASCORE_BASE}/tennis/",
        "User-Agent": random.choice(UAS),
    }
    if extra:
        headers.update(extra)
    async with httpx.AsyncClient(http2=False, timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 403:
            return None
        response.raise_for_status()
        return response.json()


async def events_by_date(day: dt.date) -> Dict[str, Any]:
    url = f"{SOFASCORE_BASE}/api/v1/sport/tennis/scheduled-events/{day.isoformat()}"
    try:
        data = await _fetch_json(url)
    except Exception as exc:
        print(f"[WARN] sofascore fallback fetch failed for {day}: {exc}")
        return {"source": "sofascore", "events": []}
    if not data:
        print(f"[WARN] sofascore fallback returned no data for {day}")
        return {"source": "sofascore", "events": []}
    events = data.get("events") or []
    if not isinstance(events, list):
        events = []
    return {"source": "sofascore", "events": events}
