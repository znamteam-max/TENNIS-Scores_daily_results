# gha_worker.py
from __future__ import annotations
import asyncio, os
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx

from db_pg import ensure_schema, set_events_cache
from providers import sofascore as ss

APP_TZ = os.getenv("APP_TZ", "Europe/Helsinki")

def _client() -> httpx.AsyncClient:
    common = dict(headers=ss.DEFAULT_HEADERS, follow_redirects=True, timeout=20.0)
    try:
        import h2  # noqa
        return httpx.AsyncClient(http2=True, **common)
    except Exception:
        return httpx.AsyncClient(**common)

async def cache_today_schedule():
    tz = ZoneInfo(APP_TZ)
    ds = datetime.now(tz).date()
    async with _client() as client:
        events = await ss.events_by_date(client, ds)
    set_events_cache(ds, events)
    print(f"[cache] stored {len(events)} events for {ds}")

async def run_once():
    ensure_schema()
    try:
        await cache_today_schedule()
    except Exception as e:
        print(f"[cache] failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_once())
