from __future__ import annotations

import datetime as dt
import json
import os
import re
import traceback
import unicodedata
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from providers import sofascore as ss


APP_TZ = os.getenv("APP_TZ", "Europe/Moscow")
MAX_DAYS = 45
DEFAULT_TOURNAMENT = "roland_garros"
FANTASY_RESULTS_SECRET = (
    os.getenv("FANTASY_RESULTS_SECRET", "").strip() or os.getenv("CRON_SECRET", "").strip()
)


def _json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "public, max-age=30, s-maxage=30")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _today() -> dt.date:
    try:
        return dt.datetime.now(ZoneInfo(APP_TZ)).date()
    except Exception:
        return dt.date.today()


def _parse_day(value: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except Exception:
        return None


def _date_range(start: dt.date, end: dt.date) -> List[dt.date]:
    if end < start:
        start, end = end, start
    delta = (end - start).days + 1
    delta = max(1, min(delta, MAX_DAYS))
    return [start + dt.timedelta(days=offset) for offset in range(delta)]


def _read_cached_day(day: dt.date) -> Dict[str, Any]:
    try:
        from db_pg import get_events_cache

        cached = get_events_cache(day)
        if cached and isinstance(cached, dict):
            events = cached.get("events") or []
            return {
                "cache_hit": bool(events),
                "source": str(cached.get("source") or "cache"),
                "events": events if isinstance(events, list) else [],
            }
        return {"cache_hit": False, "source": "cache_miss", "events": []}
    except Exception as exc:
        return {"cache_hit": False, "source": "cache_error", "events": [], "error": str(exc)}


def _to_bool(raw: Any, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tournament_hay(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    return _plain(
        " ".join(
            str(value or "")
            for value in (
                event.get("tournament_name"),
                event.get("season_name"),
                event.get("tournament_status"),
                raw.get("flashscore_league"),
                raw.get("eventName"),
                raw.get("name"),
                raw.get("stage"),
                raw.get("round"),
            )
        )
    )


def _matches_filter(hay: str, pattern: str) -> bool:
    if not pattern:
        return True
    try:
        return bool(re.search(pattern, hay, flags=re.IGNORECASE))
    except re.error:
        return _plain(pattern) in hay


def _is_roland_target(value: str) -> bool:
    normalized = _plain(value).replace("_", " ").replace("-", " ")
    return normalized in {
        "roland garros",
        "french open",
        "france open",
        "rg",
    }


def _is_roland_hay(hay: str) -> bool:
    return any(
        token in hay
        for token in (
            "roland",
            "garros",
            "french open",
            "france open",
            "ролан",
            "гаррос",
            "франц",
        )
    )


def _is_target_tournament(event: Dict[str, Any], tournament: str, legacy_filter: str) -> bool:
    hay = _tournament_hay(event)
    if tournament:
        if _is_roland_target(tournament):
            if not _is_roland_hay(hay):
                return False
        elif not _matches_filter(hay, tournament):
            return False
    if legacy_filter and not _matches_filter(hay, legacy_filter):
        return False
    return True


def _is_doubles(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = _plain(
        " ".join(
            str(value or "")
            for value in (
                raw.get("flashscore_league"),
                event.get("season_name"),
                event.get("tournament_name"),
                event.get("tournament_status"),
                event.get("home_name"),
                event.get("away_name"),
            )
        )
    )
    return (
        "/" in str(event.get("home_name") or "")
        or "/" in str(event.get("away_name") or "")
        or "doubles" in hay
        or "double" in hay
        or "парн" in hay
    )


def _is_excluded_draw(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = _plain(
        " ".join(
            str(value or "")
            for value in (
                raw.get("flashscore_league"),
                raw.get("eventName"),
                raw.get("name"),
                raw.get("round"),
                raw.get("stage"),
                raw.get("statusDescription"),
                event.get("season_name"),
                event.get("tournament_name"),
                event.get("tournament_status"),
            )
        )
    )
    blocked = (
        "boys",
        "girls",
        "junior",
        "juniors",
        "qualification",
        "qualifying",
        "qualif",
        "wheelchair",
        "legends",
        "mixed",
        "юниор",
        "юнош",
        "девуш",
        "квалиф",
        "коляс",
        "легенд",
        "смеш",
    )
    return any(token in hay for token in blocked)


def _pair_key(left: str, right: str) -> tuple[str, str]:
    def clean(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    a = clean(left)
    b = clean(right)
    return tuple(sorted((a, b)))


STALE_PAIR_KEYS = {
    _pair_key("Дакворт Дж.", "Jodar R."),
    _pair_key("James Duckworth", "Jodar R."),
    _pair_key("Leroux J.", "Reco A."),
}


def _is_stale_pair(event: Dict[str, Any]) -> bool:
    return _pair_key(str(event.get("home_name") or ""), str(event.get("away_name") or "")) in STALE_PAIR_KEYS


def _tour_code(event: Dict[str, Any]) -> str:
    category = str(event.get("category") or "").upper()
    if category == "WTA" or event.get("tour_group") == "women":
        return "WTA"
    if category == "ATP" or event.get("tour_group") == "men":
        return "ATP"
    return str(event.get("category") or "")


def _winner_side(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    winner = raw.get("winnerCode")
    if str(winner) == "1":
        return "home"
    if str(winner) == "2":
        return "away"
    return ""


def _event_day(event: Dict[str, Any], fallback: dt.date) -> str:
    ts = event.get("start_ts")
    if isinstance(ts, int) and ts > 0:
        try:
            return dt.datetime.fromtimestamp(ts, ZoneInfo(APP_TZ)).date().isoformat()
        except Exception:
            pass
    return fallback.isoformat()


def _event_time(event: Dict[str, Any]) -> str:
    ts = event.get("start_ts")
    if isinstance(ts, int) and ts > 0:
        try:
            return dt.datetime.fromtimestamp(ts, ZoneInfo(APP_TZ)).strftime("%H:%M")
        except Exception:
            pass
    return ""


def _status(event: Dict[str, Any]) -> str:
    value = str(ss.status_type(event) or "").lower()
    if value in {"retired", "walkover"}:
        return "finished"
    if value in {"inprogress", "live"}:
        return "live"
    if value in {"finished", "ended", "complete", "final"}:
        return "finished"
    return "not_started"


def _status_filter(status: str, wanted: str) -> bool:
    wanted = (wanted or "").strip().lower()
    if not wanted or wanted in {"all", "any"}:
        return True
    if wanted in {"finished", "done"}:
        return status == "finished"
    if wanted in {"upcoming", "future"}:
        return status == "not_started"
    if wanted in {"live", "inprogress"}:
        return status == "live"
    return status == wanted


def _round(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    text = " ".join(str(raw.get(key) or event.get(key) or "") for key in ("round", "stage", "statusDescription"))
    text_l = text.lower()
    if "final" in text_l and "semi" not in text_l:
        return "F"
    if "semi" in text_l or "1/2" in text_l:
        return "SF"
    if "quarter" in text_l or "1/4" in text_l:
        return "QF"
    if "round 4" in text_l or "4th" in text_l or "1/8" in text_l:
        return "R4"
    if "round 3" in text_l or "3rd" in text_l or "1/16" in text_l:
        return "R3"
    if "round 2" in text_l or "2nd" in text_l or "1/32" in text_l:
        return "R2"
    return "R1"


def _score(event: Dict[str, Any]) -> str:
    set_scores = ss.set_scores(event)
    if set_scores:
        return " ".join(set_scores)
    return ss.compact_score(event)


def _url(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    flashscore_id = raw.get("flashscore_id") or event.get("custom_id") or ""
    if flashscore_id:
        return f"https://www.flashscorekz.com/match/{flashscore_id}/#/match-summary"
    return ""


def _winner_loser(event: Dict[str, Any], home: str, away: str) -> tuple[str, str]:
    side = _winner_side(event)
    if side == "home":
        return home, away
    if side == "away":
        return away, home
    return "", ""


def _canonical_match_key(day_iso: str, tour: str, round_code: str, player1: str, player2: str) -> str:
    left = _plain(player1)
    right = _plain(player2)
    pair = "|".join(sorted([left, right]))
    return f"{day_iso}:{tour}:{round_code}:{pair}"


def _compatibility_item(event: Dict[str, Any], day: dt.date) -> Dict[str, Any]:
    tournament = str(event.get("tournament_name") or "")
    tour = _tour_code(event)
    match_id = str(event.get("event_id") or event.get("custom_id") or "")
    status = _status(event)
    home = str(event.get("home_name") or "")
    away = str(event.get("away_name") or "")
    day_iso = _event_day(event, day)
    score = _score(event)
    round_code = _round(event)
    source = str((event.get("raw") or {}).get("source") or "flashscore")
    winner_name, loser_name = _winner_loser(event, home, away)
    singles = not _is_doubles(event)
    main_draw = not _is_excluded_draw(event)
    canonical_key = _canonical_match_key(day_iso, tour, round_code, home, away)

    return {
        # New contract
        "external_match_id": match_id,
        "canonical_match_key": canonical_key,
        "date": day_iso,
        "tournament": tournament,
        "tour": tour,
        "round": round_code,
        "status": status,
        "is_singles": singles,
        "is_main_draw": main_draw,
        "player1_name": home,
        "player2_name": away,
        "winner_name": winner_name,
        "loser_name": loser_name,
        "score": score,
        "started_at": None,
        "finished_at": None,
        "source": source,
        # Legacy compatibility for existing fantasy GAS fetchers
        "id": match_id,
        "matchId": match_id,
        "provider": source,
        "tournament_name": tournament,
        "league": f"{tour}: {tournament}" if tour and tournament else tournament,
        "startTime": _event_time(event),
        "startTimestamp": int(event.get("start_ts") or 0),
        "homeName": home,
        "awayName": away,
        "home": {"name": home},
        "away": {"name": away},
        "players": [{"side": "home", "name": home}, {"side": "away", "name": away}],
        "winnerSide": _winner_side(event),
        "url": _url(event),
        "rawStatus": str(ss.status_type(event) or ""),
    }


def _sort_key(item: Dict[str, Any]) -> tuple:
    return (
        str(item.get("date") or ""),
        int(item.get("startTimestamp") or 0),
        str(item.get("tour") or ""),
        str(item.get("player1_name") or item.get("homeName") or ""),
    )


def _is_authorized(handler: BaseHTTPRequestHandler, query: Dict[str, List[str]]) -> bool:
    if not FANTASY_RESULTS_SECRET:
        return True
    expected = f"Bearer {FANTASY_RESULTS_SECRET}"
    if handler.headers.get("authorization", "") == expected:
        return True
    return (query.get("secret") or [""])[0] == FANTASY_RESULTS_SECRET


def _resolve_range(query: Dict[str, List[str]]) -> tuple[Optional[dt.date], Optional[dt.date], Optional[str]]:
    from_raw = (query.get("from") or [""])[0]
    to_raw = (query.get("to") or [""])[0]
    if from_raw or to_raw:
        start = _parse_day(from_raw) if from_raw else None
        end = _parse_day(to_raw) if to_raw else None
        if from_raw and not start:
            return None, None, "invalid 'from' date"
        if to_raw and not end:
            return None, None, "invalid 'to' date"
        if start and not end:
            end = start
        if end and not start:
            start = end
        return start, end, None

    legacy_start = _parse_day((query.get("date") or [""])[0]) or _today()
    days_raw = (query.get("days") or ["1"])[0]
    try:
        days = max(1, min(int(days_raw or 1), MAX_DAYS))
    except Exception:
        return None, None, "invalid 'days' value"
    legacy_end = legacy_start + dt.timedelta(days=days - 1)
    return legacy_start, legacy_end, None


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            if not _is_authorized(self, query):
                _json_response(self, {"ok": False, "error": "unauthorized"}, status=401)
                return

            start, end, range_error = _resolve_range(query)
            if range_error:
                _json_response(self, {"ok": False, "error": range_error}, status=400)
                return
            if not start or not end:
                _json_response(self, {"ok": False, "error": "invalid date range"}, status=400)
                return

            days = _date_range(start, end)
            if not days:
                _json_response(self, {"ok": False, "error": "empty date range"}, status=400)
                return

            tournament = (query.get("tournament") or [DEFAULT_TOURNAMENT])[0]
            singles_only = _to_bool((query.get("singles_only") or ["1"])[0], default=True)
            wanted_status = (query.get("status") or ["all"])[0]
            legacy_filter = (query.get("filter") or [""])[0]

            matches: List[Dict[str, Any]] = []
            by_date: Dict[str, Dict[str, Any]] = {}
            sources: Dict[str, int] = {}
            total_raw_events = 0

            for day in days:
                day_iso = day.isoformat()
                payload = _read_cached_day(day)
                source = str(payload.get("source") or "unknown")
                sources[source] = sources.get(source, 0) + 1

                if payload.get("error"):
                    by_date[day_iso] = {
                        "raw": 0,
                        "returned": 0,
                        "finished": 0,
                        "live": 0,
                        "not_started": 0,
                        "cache_hit": False,
                        "error": str(payload.get("error")),
                    }
                    continue

                events = ss.normalize_events({"events": payload.get("events") or []})
                total_raw_events += len(events)
                stats = {
                    "raw": len(events),
                    "returned": 0,
                    "finished": 0,
                    "live": 0,
                    "not_started": 0,
                    "cache_hit": bool(payload.get("cache_hit")),
                }

                for event in events:
                    if not _is_target_tournament(event, tournament, legacy_filter):
                        continue
                    if singles_only and _is_doubles(event):
                        continue
                    if _is_excluded_draw(event):
                        continue
                    if _is_stale_pair(event):
                        continue

                    item = _compatibility_item(event, day)
                    status = str(item.get("status") or "not_started")
                    if not _status_filter(status, wanted_status):
                        continue

                    matches.append(item)
                    stats["returned"] += 1
                    if status == "finished":
                        stats["finished"] += 1
                    elif status == "live":
                        stats["live"] += 1
                    else:
                        stats["not_started"] += 1

                by_date[day_iso] = stats

            matches.sort(key=_sort_key)
            response: Dict[str, Any] = {
                "ok": True,
                "source": "tennis-scores-daily-results-1",
                "from": days[0].isoformat(),
                "to": days[-1].isoformat(),
                "count": len(matches),
                "matches": matches,
                "by_date": by_date,
                # Compatibility fields for existing GAS
                "date": days[0].isoformat(),
                "days": len(days),
                "sources": sources,
                "items": matches,
                "events": matches,
                # Helpful diagnostics
                "params": {
                    "tournament": tournament,
                    "singles_only": singles_only,
                    "status": wanted_status,
                    "filter": legacy_filter,
                },
                "raw_events_total": total_raw_events,
            }
            _json_response(self, response, status=200)
        except Exception as exc:
            _json_response(
                self,
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc().splitlines()[-8:],
                },
                status=500,
            )
