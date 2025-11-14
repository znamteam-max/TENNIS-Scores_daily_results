# gha_worker.py
import asyncio
from datetime import date
import httpx
from providers import sofascore as ss
from db_pg import set_events_cache

async def run_once():
    async with httpx.AsyncClient(headers=ss.DEFAULT_HEADERS, follow_redirects=True, timeout=30) as c:
        today = date.today()
        events = await ss.events_by_date(c, today)
        set_events_cache(today, events)

if __name__ == "__main__":
    asyncio.run(run_once())
