from __future__ import annotations

import asyncio
import os
import datetime as dt
from zoneinfo import ZoneInfo
import httpx

from db_pg import ensure_schema, set_events_cache
from providers import sofascore as ss


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TZ", "Europe/London"))


def today_local() -> dt.date:
    return dt.datetime.now(_tz()).date()


def _date_in_tz(ts: int, tz: ZoneInfo) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz).date()


async def run_once() -> None:
    ensure_schema()
    d = today_local()
    tz = _tz()
    async with httpx.AsyncClient(http2=True, timeout=25.0) as c:
        try:
            raw = await ss.events_by_date(c, d)
        except Exception as e:
            print(f"[ERR] sofascore fetch failed: {e}")
            raw = {}

    events = []
    for ev in (raw.get("events") or []):
        ts = ev.get("startTimestamp")
        if isinstance(ts, int) and _date_in_tz(ts, tz) == d:
            events.append(ev)

    data = {"events": events}
    set_events_cache(d, data)
    print(f"[OK] cache updated for {d}, events={len(events)}")


if __name__ == "__main__":
    asyncio.run(run_once())
