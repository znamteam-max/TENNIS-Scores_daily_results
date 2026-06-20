from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
import urllib.parse
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
DEFAULT_FANTASY_SYNC_ACTIONS = "auto"
DEFAULT_FANTASY_SYNC_URL = (
    "https://script.google.com/macros/s/"
    "AKfycby-z8GyLJtqCF0Mm4zKa0uObgzaV0wUMzVHn3ZTeBIdBCLRwTozm8FSTvah-iZ_yw3e6A/exec"
)
DEFAULT_FANTASY_ADMIN_ID = "52203584"
_FINISHED_GATE_SEEN: dict[str, set[int]] = {}


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


def _fantasy_sync_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    overrides = overrides or {}
    actions_raw = overrides.get("actions") or os.getenv("FANTASY_SYNC_ACTIONS", DEFAULT_FANTASY_SYNC_ACTIONS)
    actions_disabled = str(actions_raw or "").strip().lower() in {"0", "off", "none", "skip", "false"}
    if actions_disabled:
        actions = []
    else:
        actions = [item.strip() for item in str(actions_raw or "").split(",") if item.strip()]
    if not actions_disabled and (not actions or "auto" in actions):
        actions = ["refresh_matches"] if dt.datetime.now(_tz()).minute % 2 == 0 else ["send_notification_queue"]
    return {
        "url": str(overrides.get("url") or os.getenv("FANTASY_SYNC_URL", DEFAULT_FANTASY_SYNC_URL)).strip(),
        "key": str(overrides.get("key") or os.getenv("FANTASY_ADMIN_ACTION_KEY", "")).strip(),
        "admin_id": str(overrides.get("admin_id") or os.getenv("FANTASY_ADMIN_ID", DEFAULT_FANTASY_ADMIN_ID)).strip(),
        "actions": actions,
    }


def _fantasy_sync_action(action: str, config: Dict[str, Any]) -> Dict[str, Any]:
    sync_url = config.get("url") or ""
    action_key = config.get("key") or ""
    admin_id = config.get("admin_id") or ""
    if not (sync_url and action_key and admin_id):
        return {"action": action, "ok": False, "skipped": "not_configured"}

    params = {
        "adminAction": action,
        "key": action_key,
        "adminId": admin_id,
        "light": "1",
    }
    # Keep poll-triggered refresh lean to avoid platform timeouts.
    if action == "refresh_matches":
        params["quick"] = "1"

    query = urllib.parse.urlencode(params)
    separator = "&" if "?" in sync_url else "?"
    url = f"{sync_url}{separator}{query}"
    try:
        with urllib.request.urlopen(url, timeout=110) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        print(f"[OK] fantasy sync action={action} ok={data.get('ok')}")
        return {"action": action, **data}
    except Exception as exc:
        print(f"[WARN] fantasy sync failed action={action}: {exc}")
        return {"action": action, "ok": False, "error": str(exc)}


def sync_fantasy_results(config_overrides: Optional[Dict[str, Any]] = None) -> list[Dict[str, Any]]:
    config = _fantasy_sync_config(config_overrides)
    if not config["actions"]:
        return []
    return [_fantasy_sync_action(action, config) for action in config["actions"]]


async def _fetch_and_cache(day: dt.date) -> dict:
    try:
        data = await ss.events_by_date(day)
    except Exception as e:
        print(f"[ERR] sofascore fetch failed for {day}: {e}")
        data = {"events": []}

    set_events_cache(day, data or {"events": []})
    print(f"[OK] cache updated for {day}, events={len((data or {}).get('events', []))}")
    return data or {"events": []}


async def _fetch_sources(day: dt.date, *, cache: bool = True) -> tuple[dict, dict, dict]:
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

    if cache:
        set_events_cache(day, primary)
    print(
        "[OK] source fetch for "
        f"{day}, cache={'yes' if cache else 'no'}, "
        f"flashscore={len((primary or {}).get('events', []) or [])}, "
        f"sofascore={len((sofascore or {}).get('events', []) or [])}, "
        f"espn={len((espn or {}).get('events', []) or [])}"
    )
    return primary, sofascore, espn


def _source_count(data: dict) -> int:
    return len((data or {}).get("events", []) or [])


def _source_row(day: dt.date, data: dict, sofascore_data: dict, espn_data: dict) -> dict[str, Any]:
    return {
        "day": day.isoformat(),
        "flashscore": _source_count(data),
        "sofascore": _source_count(sofascore_data),
        "espn": _source_count(espn_data),
    }


def _db_gate_mode() -> str:
    return (os.getenv("POLL_DB_WAKE_MODE") or "finish_gate").strip().lower().replace("-", "_")


def _db_gate_enabled(days: Optional[Iterable[dt.date]], debug: bool) -> bool:
    if debug or days is not None:
        return False
    return _db_gate_mode() not in {"0", "off", "false", "no", "always", "db_first"}


def _finished_ids_from_sources(data: dict, sofascore_data: dict, espn_data: dict) -> set[int]:
    finished: set[int] = set()
    for source in (data, sofascore_data, espn_data):
        for event in ss.normalize_events(source):
            try:
                event_id = int(event["event_id"])
            except Exception:
                continue
            if ss.is_finished(event) and ss.has_result_winner(event):
                finished.add(event_id)
    return finished


def _remember_finished(day: dt.date, finished_ids: set[int]) -> set[int]:
    key = day.isoformat()
    today = today_local()
    for existing in list(_FINISHED_GATE_SEEN):
        try:
            existing_day = dt.date.fromisoformat(existing)
        except Exception:
            existing_day = today
        if existing_day < today - dt.timedelta(days=3):
            _FINISHED_GATE_SEEN.pop(existing, None)

    seen = _FINISHED_GATE_SEEN.setdefault(key, set())
    new_ids = {event_id for event_id in finished_ids if event_id not in seen}
    seen.update(finished_ids)
    return new_ids


async def _probe_sources_without_db(run_days: set[dt.date]) -> tuple[dict[dt.date, tuple[dict, dict, dict]], list[dict[str, Any]], bool]:
    snapshots: dict[dt.date, tuple[dict, dict, dict]] = {}
    rows: list[dict[str, Any]] = []
    should_open_db = False
    for day in sorted(run_days, reverse=True):
        data, sofascore_data, espn_data = await _fetch_sources(day, cache=False)
        snapshots[day] = (data, sofascore_data, espn_data)
        finished_ids = _finished_ids_from_sources(data, sofascore_data, espn_data)
        new_finished_ids = _remember_finished(day, finished_ids)
        if new_finished_ids:
            should_open_db = True
        row = _source_row(day, data, sofascore_data, espn_data)
        row.update(
            {
                "db": "pending" if new_finished_ids else "skipped",
                "reason": "new_finished_events" if new_finished_ids else "no_new_finished_events",
                "finished_seen": len(finished_ids),
                "new_finished": len(new_finished_ids),
            }
        )
        rows.append(row)
    return snapshots, rows, should_open_db


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


def _pending_debug_detail(
    day: dt.date,
    watch: Dict[str, Any],
    event: Optional[Dict[str, Any]],
    reason: str,
) -> Dict[str, Any]:
    return {
        "day": day.isoformat(),
        "event_id": int(watch["event_id"]),
        "home_name": str((event or watch).get("home_name") or ""),
        "away_name": str((event or watch).get("away_name") or ""),
        "tournament_name": str((event or watch).get("tournament_name") or ""),
        "status_type": ss.status_type(event) if event else "",
        "score": ss.compact_score(event) if event else "",
        "reason_not_sent": reason,
    }


def _include_yesterday_by_default() -> bool:
    return os.getenv("POLL_INCLUDE_YESTERDAY", "").strip().lower() in {"1", "true", "yes", "on"}


async def run_once(
    days: Optional[Iterable[dt.date]] = None,
    *,
    include_yesterday: Optional[bool] = None,
    fantasy_config: Optional[Dict[str, Any]] = None,
    debug: bool = False,
) -> dict[str, Any]:
    today = today_local()
    if days is None:
        run_days = {today}
        include_previous = include_yesterday if include_yesterday is not None else _include_yesterday_by_default()
        if include_previous:
            run_days.add(today - dt.timedelta(days=1))
    else:
        run_days = set(days)

    source_snapshots: dict[dt.date, tuple[dict, dict, dict]] = {}
    if _db_gate_enabled(days, debug):
        source_snapshots, gate_sources, should_open_db = await _probe_sources_without_db(run_days)
        if not should_open_db:
            fantasy_sync = sync_fantasy_results(fantasy_config)
            print("[OK] result poll skipped DB: no new finished events")
            return {
                "sent": 0,
                "sources": gate_sources,
                "db": "skipped",
                "skip_reason": "no_new_finished_events",
                "fantasy": fantasy_sync,
            }

    ensure_schema()
    run_days.update(list_pending_match_watch_days())

    if not BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN is not set; result notifications skipped")
    if PUBLISH_CHAT_ID:
        print(f"[OK] publishing result cards to chat_id={PUBLISH_CHAT_ID}")

    sent = 0
    sources: list[dict[str, Any]] = []
    for day in sorted(run_days, reverse=True):
        if day in source_snapshots:
            data, sofascore_data, espn_data = source_snapshots[day]
            set_events_cache(day, data)
        else:
            data, sofascore_data, espn_data = await _fetch_sources(day)
        events = ss.normalize_events(data)
        fallback_events = ss.normalize_events(sofascore_data) + ss.normalize_events(espn_data)
        events_by_id = {int(e["event_id"]): e for e in events}
        odds_saved = await cache_match_odds(day, events)

        pending = list_pending_match_watches(day)
        source_row = {
            **_source_row(day, data, sofascore_data, espn_data),
            "pending": len(pending),
            "odds_saved": odds_saved,
            "db": "used",
        }
        pending_details: list[dict[str, Any]] = []
        published_events: set[tuple[dt.date, int]] = set()
        for watch in pending:
            event_key = (day, int(watch["event_id"]))
            if PUBLISH_CHAT_ID and event_key in published_events:
                if debug:
                    pending_details.append(_pending_debug_detail(day, watch, None, "already_notified"))
                continue
            event = events_by_id.get(int(watch["event_id"]))
            if event and ss.is_finished(event):
                resolved_event = event
            else:
                match_basis = event or watch
                fallback_event, reversed_sides = _best_fallback_match(match_basis, fallback_events)
                if not fallback_event:
                    if debug:
                        reason = "not_finished_in_flashscore" if event else "event_not_found_in_flashscore"
                        pending_details.append(_pending_debug_detail(day, watch, event, reason))
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
                if debug:
                    pending_details.append(_pending_debug_detail(day, watch, resolved_event, "fallback_found_but_not_finished"))
                continue
            if not ss.has_result_winner(resolved_event):
                if debug:
                    pending_details.append(_pending_debug_detail(day, watch, resolved_event, "no_result_winner"))
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
                if debug:
                    pending_details.append(_pending_debug_detail(day, watch, event, "sent"))
            elif debug:
                pending_details.append(_pending_debug_detail(day, watch, event, "send_failed"))

        summary_sent = publish_daily_summaries(day, events, BOT_TOKEN, PUBLISH_CHAT_ID)
        if summary_sent:
            print(f"[OK] daily summaries sent={summary_sent} day={day}")
        if debug:
            source_row["pending_details"] = pending_details
        sources.append(source_row)

    fantasy_sync = sync_fantasy_results(fantasy_config)
    print(f"[OK] result notifications sent={sent}")
    return {"sent": sent, "sources": sources, "fantasy": fantasy_sync}


if __name__ == "__main__":
    asyncio.run(run_once())