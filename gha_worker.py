from __future__ import annotations

import asyncio, os, datetime as dt
from zoneinfo import ZoneInfo
from db_pg import ensure_schema, set_events_cache
from providers import sofascore as ss

def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TZ", "Europe/London"))

def today_local() -> dt.date:
    return dt.datetime.now(_tz()).date()

async def run_once() -> None:
    ensure_schema()
    d = today_local()
    try:
        data = await ss.events_by_date(d)
    except Exception as e:
        print(f"[ERR] sofascore fetch failed: {e}")
        data = {}
    set_events_cache(d, data or {"events": []})
    print(f"[OK] cache updated for {d}, events={len((data or {}).get('events', []))}")

if __name__ == "__main__":
    asyncio.run(run_once())
