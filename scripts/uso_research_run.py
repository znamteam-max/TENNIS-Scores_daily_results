from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.uso_research as research


async def main() -> None:
    import datetime as dt

    # Recent-delta refresh only. Keep one transient Flashscore day failure from
    # aborting the whole research run.
    old_load_day = research._load_day

    async def safe_load_day(day):
        try:
            rows = await old_load_day(day)
            print(f"day={day} matches={len(rows)}")
            return rows
        except Exception as exc:
            print(f"day={day} load failed: {exc}")
            return []

    research._load_day = safe_load_day
    start = dt.date(2026, 9, 3)
    end = dt.date(2026, 9, 5)
    matches, meta = await research._collect(start, end, concurrency=12)
    report = research._report(matches, meta)
    report["matches"] = matches
    out = Path("uso_research_2026.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    print(f"wrote {out} bytes={out.stat().st_size}")


if __name__ == "__main__":
    asyncio.run(main())
