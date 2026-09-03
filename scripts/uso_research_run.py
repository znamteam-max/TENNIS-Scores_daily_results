from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.uso_research import _collect, _report


async def main() -> None:
    import datetime as dt

    start = dt.date(2026, 8, 24)
    end = dt.date(2026, 9, 3)
    matches, meta = await _collect(start, end, concurrency=16)
    report = _report(matches, meta)
    report["matches"] = matches
    out = Path("uso_research_2026.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    print(f"wrote {out} bytes={out.stat().st_size}")


if __name__ == "__main__":
    asyncio.run(main())
