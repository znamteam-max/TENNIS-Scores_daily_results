from __future__ import annotations

import asyncio
import os
import datetime as dt
from zoneinfo import ZoneInfo
import httpx

from db_pg import ensure_schema, set_events_cache
from providers import sofascore as ss


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TZ", "Europe/Berlin"))


def today_local() -> dt.date:
    return dt.datetime.now(_tz()).date()


async def run_once() -> None:
    ensure_schema()
    d = today_local()
    async with httpx.AsyncClient(http2=True, timeout=20.0) as c:
        try:
            data = await ss.events_by_date(c, d)
        except Exception as e:
            print(f"[ERR] sofascore fetch failed: {e}")
            data = {}
    set_events_cache(d, data or {"events": []})
    print(f"[OK] cache updated for {d}, events={len((data or {}).get('events', []))}")


if __name__ == "__main__":
    asyncio.run(run_once())
