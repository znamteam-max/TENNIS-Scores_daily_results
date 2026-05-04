from __future__ import annotations

import datetime as dt
import os
import urllib.parse
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx


BASE_URL = "https://api.the-odds-api.com"


def _api_key() -> str:
    return (os.getenv("ODDS_API_KEY") or os.getenv("THE_ODDS_API_KEY") or "").strip()


def enabled() -> bool:
    return bool(_api_key())


def _tz() -> ZoneInfo:
    return ZoneInfo(os.getenv("APP_TZ", "Europe/Helsinki"))


def _day_window_utc(day: dt.date) -> tuple[str, str]:
    tz = _tz()
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


async def _get_json(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    response = await client.get(f"{BASE_URL}{path}?{query}")
    response.raise_for_status()
    return response.json()


async def tennis_sport_keys(client: httpx.AsyncClient) -> List[str]:
    configured = [x.strip() for x in (os.getenv("ODDS_API_SPORT_KEYS") or "").split(",") if x.strip()]
    if configured:
        return configured

    sports = await _get_json(client, "/v4/sports/", {"apiKey": _api_key(), "all": "true"})
    keys: List[str] = []
    for sport in sports or []:
        if str(sport.get("group") or "").lower() != "tennis":
            continue
        if sport.get("has_outrights"):
            continue
        key = str(sport.get("key") or "").strip()
        if key and key.startswith("tennis_"):
            keys.append(key)
    return keys


async def odds_by_date(day: dt.date) -> List[Dict[str, Any]]:
    if not enabled():
        return []

    regions = (os.getenv("ODDS_API_REGIONS") or "eu").strip()
    bookmakers = (os.getenv("ODDS_API_BOOKMAKERS") or "").strip()
    markets = (os.getenv("ODDS_API_MARKETS") or "h2h").strip()
    start, end = _day_window_utc(day)
    timeout = httpx.Timeout(25.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        keys = await tennis_sport_keys(client)
        out: List[Dict[str, Any]] = []
        for key in keys:
            params: Dict[str, Any] = {
                "apiKey": _api_key(),
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
                "commenceTimeFrom": start,
                "commenceTimeTo": end,
            }
            if bookmakers:
                params["bookmakers"] = bookmakers
            else:
                params["regions"] = regions
            try:
                data = await _get_json(client, f"/v4/sports/{key}/odds/", params)
            except Exception as exc:
                print(f"[odds] fetch failed sport={key}: {exc}")
                continue
            for item in data or []:
                item["sport_key"] = item.get("sport_key") or key
                out.append(item)
        return out
