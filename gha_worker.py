import os
import asyncio
import datetime as dt

import httpx
import pytz

from db_pg import ensure_schema, cache_schedule  # cache_schedule: date -> json в БД
from providers import sofascore as ss

TZ = os.getenv("APP_TZ", "Europe/Helsinki")

def today_local() -> dt.date:
    return dt.datetime.now(pytz.timezone(TZ)).date()

async def run_once() -> None:
    ensure_schema()

    today = today_local()
    # HTTP/2 отключаем — challenge прилетает чаще; ставим нормальный UA
    async with httpx.AsyncClient(http2=False, headers={
        "User-Agent": os.getenv(
            "SOFA_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }) as client:
        try:
            events = await ss.events_by_date(client, today)
        except ss.SofascoreChallenge as e:
            print(f"[WARN] Sofascore challenge, schedule skipped: {e}")
            # Не валим job — просто выходим. Следующий запуск, как правило, отрабатывает.
            return
        except Exception as e:
            print(f"[WARN] Unexpected error while fetching schedule: {e}")
            return

    try:
        # сохраняем кэш в БД одним JSON-blob’ом на дату
        cache_schedule(today, events)
        print(f"[OK] Cached schedule for {today}: {len(events)} events")
    except Exception as e:
        print(f"[WARN] Could not cache schedule: {e}")

if __name__ == "__main__":
    asyncio.run(run_once())
