from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from db_pg import (
    ensure_schema,
    list_pending_match_watch_days,
    list_pending_match_watches,
    mark_match_notified,
    set_events_cache,
)
from providers import sofascore as ss
from telegram_media import send_match_result


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PUBLISH_CHAT_ID = (
    os.getenv("PUBLISH_CHAT_ID")
    or os.getenv("RESULTS_CHAT_ID")
    or os.getenv("TELEGRAM_PUBLISH_CHAT_ID")
    or ""
).strip()


def _publish_chat_id(chat_id: int) -> int | str:
    return PUBLISH_CHAT_ID or chat_id


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TZ", "Europe/London"))


def today_local() -> dt.date:
    return dt.datetime.now(_tz()).date()


def _tg_send_message(chat_id: int, text: str) -> bool:
    if not BOT_TOKEN:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except urllib.error.URLError as e:
        print(f"[tg] send_message failed for chat_id={chat_id}: {e}")
        return False


async def _fetch_and_cache(day: dt.date) -> dict:
    try:
        data = await ss.events_by_date(day)
    except Exception as e:
        print(f"[ERR] sofascore fetch failed for {day}: {e}")
        data = {"events": []}

    set_events_cache(day, data or {"events": []})
    print(f"[OK] cache updated for {day}, events={len((data or {}).get('events', []))}")
    return data or {"events": []}


async def run_once() -> None:
    ensure_schema()

    days = {today_local()}
    days.update(list_pending_match_watch_days())

    if not BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN is not set; result notifications skipped")
    if PUBLISH_CHAT_ID:
        print(f"[OK] publishing result cards to chat_id={PUBLISH_CHAT_ID}")

    sent = 0
    for day in sorted(days):
        data = await _fetch_and_cache(day)
        events = ss.normalize_events(data)
        events_by_id = {int(e["event_id"]): e for e in events}

        pending = list_pending_match_watches(day)
        for watch in pending:
            event = events_by_id.get(int(watch["event_id"]))
            if not event or not ss.is_finished(event):
                continue

            event = await ss.enrich_event(event)
            source_chat_id = int(watch["chat_id"])
            if send_match_result(
                BOT_TOKEN,
                _publish_chat_id(source_chat_id),
                event,
                review_chat_id=source_chat_id,
                allow_text_fallback=False,
            ):
                if mark_match_notified(source_chat_id, day, int(watch["event_id"])):
                    sent += 1

    print(f"[OK] result notifications sent={sent}")

if __name__ == "__main__":
    asyncio.run(run_once())
