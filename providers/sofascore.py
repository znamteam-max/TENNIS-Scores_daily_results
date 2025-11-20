from __future__ import annotations

import datetime as dt
import random
import httpx
from typing import Dict, Any

BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}


async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    h = dict(HEADERS)
    h["User-Agent"] = random.choice(UAS)
    r = await client.get(url, headers=h, timeout=20.0)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {}


def _ds(d: dt.date) -> str:
    return d.isoformat()


async def events_by_date(client: httpx.AsyncClient, d: dt.date) -> Dict[str, Any]:
    """
    Возвращает {"events":[...]} или пустой словарь.
    Делает ретраи по двум базам и несколько разных путей в случае 403.
    """
    paths = [
        f"/sport/tennis/scheduled-events/{_ds(d)}",
        f"/sport/tennis/events/{_ds(d)}",  # запасной
    ]
    last_exc = None
    for base in BASES:
        for path in paths:
            try:
                data = await _get_json(client, f"{base}{path}")
                if data:
                    return data
            except httpx.HTTPError as e:
                last_exc = e
                continue
    # запасной источник — live
    try:
        data = await _get_json(client, f"{BASES[0]}/sport/tennis/events/live")
        if data:
            return data
    except Exception:
        pass
    if last_exc:
        raise last_exc
    return {}
