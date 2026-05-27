from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from providers import sofascore as ss


APP_TZ = "Europe/Moscow"
MAX_DAYS = 7


def _json_response(handler: BaseHTTPRequestHandler, payload: Dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "public, max-age=45, s-maxage=45")
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


def _date_range(start: dt.date, days: int) -> List[dt.date]:
    days = max(1, min(int(days or 1), MAX_DAYS))
    return [start + dt.timedelta(days=offset) for offset in range(days)]


def _load_day(day: dt.date, refresh: bool) -> Dict[str, Any]:
    if not refresh:
        try:
            from db_pg import get_events_cache

            cached = get_events_cache(day)
            if cached and cached.get("events"):
                return {"source": cached.get("source") or "cache", "events": cached.get("events") or []}
        except Exception:
            pass

    data = asyncio.run(ss.events_by_date(day)) or {"source": "flashscore", "events": []}
    try:
        from db_pg import set_events_cache

        set_events_cache(day, data)
    except Exception:
        pass
    return data


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


def _tour_code(event: Dict[str, Any]) -> str:
    if str(event.get("category") or "").upper() == "WTA" or event.get("tour_group") == "women":
        return "WTA"
    if str(event.get("category") or "").upper() == "ATP" or event.get("tour_group") == "men":
        return "ATP"
    return str(event.get("category") or "")


def _canonical_tournament(event: Dict[str, Any]) -> str:
    raw = " ".join(
        str(event.get(key) or "")
        for key in ("tournament_name", "season_name", "tournament_status")
    ).lower()
    if any(token in raw for token in ("roland", "garros", "french open", "france open", "франц", "гаррос")):
        return "Roland Garros"
    return str(event.get("tournament_name") or "")


def _is_doubles(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = " ".join(
        str(value or "")
        for value in (
            raw.get("flashscore_league"),
            event.get("season_name"),
            event.get("tournament_name"),
            event.get("tournament_status"),
            event.get("home_name"),
            event.get("away_name"),
        )
    ).lower()
    return "/" in str(event.get("home_name") or "") or "/" in str(event.get("away_name") or "") or "парн" in hay or "doubles" in hay


def _is_excluded_draw(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = " ".join(
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
    ).lower()
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
    _pair_key("Джонс Ф.", "Боузкова М."),
    _pair_key("Jones F.", "Bouzkova M."),
    _pair_key("Francesca Jones", "Bouzkova M."),
    _pair_key("Leroux J.", "Reco A."),
}


def _is_stale_pair(event: Dict[str, Any]) -> bool:
    return _pair_key(str(event.get("home_name") or ""), str(event.get("away_name") or "")) in STALE_PAIR_KEYS


def _winner_side(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    winner = raw.get("winnerCode")
    if str(winner) == "1":
        return "home"
    if str(winner) == "2":
        return "away"
    return ""


def _status(event: Dict[str, Any]) -> str:
    value = ss.status_type(event)
    if value in {"retired", "walkover"}:
        return "finished"
    return value or ""


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


def _url(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    flashscore_id = raw.get("flashscore_id") or event.get("custom_id") or ""
    if flashscore_id:
        return f"https://www.flashscorekz.com/match/{flashscore_id}/#/match-summary"
    return ""


def _item(event: Dict[str, Any], day: dt.date) -> Dict[str, Any]:
    tournament = _canonical_tournament(event)
    tour = _tour_code(event)
    match_id = str(event.get("event_id") or event.get("custom_id") or "")
    set_scores = ss.set_scores(event)
    score = " ".join(set_scores) if set_scores else ss.compact_score(event)
    status = _status(event)
    home = str(event.get("home_name") or "")
    away = str(event.get("away_name") or "")
    winner_side = _winner_side(event)

    return {
        "id": match_id,
        "matchId": match_id,
        "provider": "flashscore",
        "tournament": tournament,
        "tournament_name": tournament,
        "league": f"{tour}: {tournament}" if tour and tournament else tournament,
        "tour": tour,
        "round": _round(event),
        "status": status,
        "date": _event_day(event, day),
        "startTime": _event_time(event),
        "startTimestamp": event.get("start_ts") or 0,
        "homeName": home,
        "awayName": away,
        "home": {"name": home},
        "away": {"name": away},
        "players": [{"side": "home", "name": home}, {"side": "away", "name": away}],
        "winnerSide": winner_side,
        "score": score,
        "url": _url(event),
        "rawStatus": ss.status_type(event),
    }


def _matches_filter(item: Dict[str, Any], pattern: str) -> bool:
    if not pattern:
        return True
    hay = " ".join(
        str(item.get(key) or "")
        for key in ("tournament", "league", "tour", "homeName", "awayName")
    )
    try:
        return bool(re.search(pattern, hay, flags=re.IGNORECASE))
    except re.error:
        return pattern.lower() in hay.lower()


def _status_filter(item: Dict[str, Any], wanted: str) -> bool:
    wanted = (wanted or "").strip().lower()
    if not wanted or wanted in {"all", "any"}:
        return True
    status = str(item.get("status") or "").lower()
    if wanted in {"finished", "done"}:
        return status in {"finished", "ended", "complete", "final"}
    if wanted in {"upcoming", "future"}:
        return status not in {"finished", "ended", "complete", "final"}
    if wanted in {"live", "inprogress"}:
        return status in {"inprogress", "live"}
    return status == wanted


def _sort_key(item: Dict[str, Any]) -> tuple:
    return (
        str(item.get("date") or ""),
        int(item.get("startTimestamp") or 0),
        str(item.get("tour") or ""),
        str(item.get("homeName") or ""),
    )


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            start = _parse_day((query.get("date") or [""])[0]) or _today()
            days = int((query.get("days") or ["1"])[0] or 1)
            refresh = (query.get("refresh") or ["0"])[0].lower() in {"1", "true", "yes", "on"}
            pattern = (query.get("filter") or [""])[0]
            wanted_status = (query.get("status") or ["all"])[0]

            items: List[Dict[str, Any]] = []
            sources: Dict[str, int] = {}
            for day in _date_range(start, days):
                data = _load_day(day, refresh)
                source = str(data.get("source") or "unknown")
                sources[source] = sources.get(source, 0) + 1
                events = ss.normalize_events(data)
                for event in events:
                    if _is_doubles(event) or _is_excluded_draw(event) or _is_stale_pair(event):
                        continue
                    item = _item(event, day)
                    if not _matches_filter(item, pattern):
                        continue
                    if not _status_filter(item, wanted_status):
                        continue
                    items.append(item)

            items.sort(key=_sort_key)
            _json_response(
                self,
                {
                    "ok": True,
                    "source": "tennis-scores-daily-results-1",
                    "sources": sources,
                    "date": start.isoformat(),
                    "days": max(1, min(days, MAX_DAYS)),
                    "count": len(items),
                    "items": items,
                    "events": items,
                },
            )
        except Exception as exc:
            _json_response(
                self,
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc().splitlines()[-6:],
                },
                status=500,
            )
