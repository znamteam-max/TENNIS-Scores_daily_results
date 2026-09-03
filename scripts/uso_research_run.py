from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.uso_research as research
from providers import sofascore as ss


async def _install_cached_day_fallback() -> None:
    if not os.getenv("POSTGRES_URL") and not os.getenv("DATABASE_URL"):
        print("POSTGRES_URL not present: using live Flashscore date feeds only")
        return
    try:
        import db_pg
        db_pg.ping_db()
    except Exception as exc:
        print(f"DB cache unavailable: {exc}")
        return

    old_load_day = research._load_day

    async def load_day(day):
        live = await old_load_day(day)
        merged = {str((e.get("raw") or {}).get("flashscore_id") or e.get("custom_id") or e.get("event_id")): e for e in live}
        try:
            cached = db_pg.get_events_cache(day) or {"events": []}
            for event in ss.normalize_events(cached):
                if not research._is_uso(event) or not ss.has_result_winner(event):
                    continue
                key = str((event.get("raw") or {}).get("flashscore_id") or event.get("custom_id") or event.get("event_id"))
                merged.setdefault(key, event)
        except Exception as exc:
            print(f"cache read failed day={day}: {exc}")
        print(f"day={day} live={len(live)} merged={len(merged)}")
        return list(merged.values())

    research._load_day = load_day


async def main() -> None:
    import datetime as dt

    await _install_cached_day_fallback()
    start = dt.date(2026, 8, 24)
    end = dt.date(2026, 9, 3)
    matches, meta = await research._collect(start, end, concurrency=16)
    report = research._report(matches, meta)
    report["matches"] = matches
    out = Path("uso_research_2026.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    print(f"wrote {out} bytes={out.stat().st_size}")


if __name__ == "__main__":
    asyncio.run(main())
