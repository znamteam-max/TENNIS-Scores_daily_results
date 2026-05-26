from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from daily_summary import cache_match_odds, publish_daily_summaries
from db_pg import (
    ensure_schema,
    list_pending_match_watch_days,
    list_pending_match_watches,
    mark_event_notified,
    mark_match_notified,
    set_events_cache,
)
from providers import espn_fallback
from providers import sofascore as ss
from providers import sofascore_fallback
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


async def _fetch_sources(day: dt.date) -> tuple[dict, dict, dict]:
    primary_result, sofascore_result, espn_result = await asyncio.gather(
        ss.events_by_date(day),
        sofascore_fallback.events_by_date(day),
        espn_fallback.events_by_date(day),
        return_exceptions=True,
    )

    if isinstance(primary_result, Exception):
        print(f"[ERR] flashscore fetch failed for {day}: {primary_result}")
        primary = {"source": "flashscore", "events": []}
    else:
        primary = primary_result or {"source": "flashscore", "events": []}

    if isinstance(sofascore_result, Exception):
        print(f"[WARN] sofascore fallback fetch failed for {day}: {sofascore_result}")
        sofascore = {"source": "sofascore", "events": []}
    else:
        sofascore = sofascore_result or {"source": "sofascore", "events": []}

    if isinstance(espn_result, Exception):
        print(f"[WARN] espn fallback fetch failed for {day}: {espn_result}")
        espn = {"source": "espn", "events": []}
    else:
        espn = espn_result or {"source": "espn", "events": []}

    set_events_cache(day, primary)
    print(
        "[OK] cache updated for "
        f"{day}, flashscore={len((primary or {}).get('events', []) or [])}, "
        f"sofascore={len((sofascore or {}).get('events', []) or [])}, "
        f"espn={len((espn or {}).get('events', []) or [])}"
    )
    return primary, sofascore, espn


def _norm_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return {token for token in re.findall(r"[a-zа-яё0-9]+", text) if len(token) > 1}


def _name_match(left: Any, right: Any) -> bool:
    left_tokens = _norm_tokens(left)
    right_tokens = _norm_tokens(right)
    return bool(left_tokens and right_tokens and left_tokens.intersection(right_tokens))


def _tournament_match(left: Any, right: Any) -> bool:
    left_tokens = _norm_tokens(left)
    right_tokens = _norm_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens.intersection(right_tokens))


def _time_match(left: Any, right: Any, max_delta_seconds: int = 12 * 60 * 60) -> bool:
    try:
        return abs(int(left) - int(right)) <= max_delta_seconds
    except Exception:
        return False


def _candidate_score(watch: Dict[str, Any], fallback: Dict[str, Any]) -> tuple[int, bool]:
    direct = _name_match(watch.get("home_name"), fallback.get("home_name")) and _name_match(
        watch.get("away_name"), fallback.get("away_name")
    )
    reversed_sides = _name_match(watch.get("home_name"), fallback.get("away_name")) and _name_match(
        watch.get("away_name"), fallback.get("home_name")
    )
    if not direct and not reversed_sides:
        return 0, False

    score = 6
    if _tournament_match(watch.get("tournament_name"), fallback.get("tournament_name")):
        score += 2
    if _time_match(watch.get("start_ts"), fallback.get("start_ts")):
        score += 2
    return score, reversed_sides


def _best_fallback_match(
    watch: Dict[str, Any],
    fallback_events: list[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], bool]:
    best: Optional[Dict[str, Any]] = None
    best_score = 0
    best_reversed = False
    for event in fallback_events:
        if not ss.is_finished(event):
            continue
        score, reversed_sides = _candidate_score(watch, event)
        if score > best_score:
            best = event
            best_score = score
            best_reversed = reversed_sides
    return best, best_reversed


def _copy_finished_state(target: Dict[str, Any], source: Dict[str, Any], reversed_sides: bool = False) -> Dict[str, Any]:
    raw_target = target.setdefault("raw", {})
    raw_source = source.get("raw") or {}
    source_status = raw_source.get("status")
    if source_status:
        raw_target["status"] = json.loads(json.dumps(source_status, ensure_ascii=False, default=str))
    target["status_type"] = ss.status_type(source)

    home_score = json.loads(json.dumps(raw_source.get("homeScore") or {}, ensure_ascii=False, default=str))
    away_score = json.loads(json.dumps(raw_source.get("awayScore") or {}, ensure_ascii=False, default=str))
    raw_target["homeScore"] = away_score if reversed_sides else home_score
    raw_target["awayScore"] = home_score if reversed_sides else away_score

    winner = raw_source.get("winnerCode")
    if reversed_sides and str(winner) in {"1", "2"}:
        winner = 2 if str(winner) == "1" else 1
    if winner is not None:
        raw_target["winnerCode"] = winner
    return target


def _include_yesterday_by_default() -> bool:
    return os.getenv("POLL_INCLUDE_YESTERDAY", "").strip().lower() in {"1", "true", "yes", "on"}


async def run_once(days: Optional[Iterable[dt.date]] = None, *, include_yesterday: Optional[bool] = None) -> dict[str, Any]:
    ensure_schema()

    today = today_local()
    if days is None:
        run_days = {today}
        include_previous = include_yesterday if include_yesterday is not None else _include_yesterday_by_default()
        if include_previous:
            run_days.add(today - dt.timedelta(days=1))
    else:
        run_days = set(days)
    run_days.update(list_pending_match_watch_days())

    if not BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN is not set; result notifications skipped")
    if PUBLISH_CHAT_ID:
        print(f"[OK] publishing result cards to chat_id={PUBLISH_CHAT_ID}")

    sent = 0
    sources: list[dict[str, Any]] = []
    for day in sorted(run_days, reverse=True):
        data, sofascore_data, espn_data = await _fetch_sources(day)
        events = ss.normalize_events(data)
        fallback_events = ss.normalize_events(sofascore_data) + ss.normalize_events(espn_data)
        events_by_id = {int(e["event_id"]): e for e in events}
        odds_saved = await cache_match_odds(day, events)

        pending = list_pending_match_watches(day)
        sources.append(
            {
                "day": day.isoformat(),
                "flashscore": len((data or {}).get("events", []) or []),
                "sofascore": len((sofascore_data or {}).get("events", []) or []),
                "espn": len((espn_data or {}).get("events", []) or []),
                "pending": len(pending),
                "odds_saved": odds_saved,
            }
        )
        published_events: set[tuple[dt.date, int]] = set()
        for watch in pending:
            event_key = (day, int(watch["event_id"]))
            if PUBLISH_CHAT_ID and event_key in published_events:
                continue
            event = events_by_id.get(int(watch["event_id"]))
            if event and ss.is_finished(event):
                resolved_event = event
            else:
                match_basis = event or watch
                fallback_event, reversed_sides = _best_fallback_match(match_basis, fallback_events)
                if not fallback_event:
                    continue
                if event:
                    resolved_event = _copy_finished_state(event, fallback_event, reversed_sides)
                    print(
                        "[OK] resolved finished match via sofascore fallback "
                        f"event_id={watch['event_id']} fallback_id={fallback_event.get('event_id')}"
                    )
                else:
                    resolved_event = fallback_event
                    print(
                        "[OK] found missing finished match via sofascore fallback "
                        f"event_id={watch['event_id']} fallback_id={fallback_event.get('event_id')}"
                    )

            if not ss.is_finished(resolved_event):
                continue

            event = await ss.enrich_event(resolved_event)
            source_chat_id = int(watch["chat_id"])
            if send_match_result(
                BOT_TOKEN,
                _publish_chat_id(source_chat_id),
                event,
                review_chat_id=source_chat_id,
                review_in_publish_chat=bool(PUBLISH_CHAT_ID),
                allow_text_fallback=False,
            ):
                if PUBLISH_CHAT_ID:
                    published_events.add(event_key)
                    if mark_event_notified(day, int(watch["event_id"])):
                        sent += 1
                elif mark_match_notified(source_chat_id, day, int(watch["event_id"])):
                    sent += 1

        summary_sent = publish_daily_summaries(day, events, BOT_TOKEN, PUBLISH_CHAT_ID)
        if summary_sent:
            print(f"[OK] daily summaries sent={summary_sent} day={day}")

    print(f"[OK] result notifications sent={sent}")
    return {"sent": sent, "sources": sources}

if __name__ == "__main__":
    asyncio.run(run_once())
