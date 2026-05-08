from __future__ import annotations

import asyncio
import json
import os
import random
import re
import statistics
from typing import Any, Dict, List, Optional

import httpx

from providers import sofascore as ss


ODDS_URL = (os.getenv("FLASHSCORE_ODDS_URL") or "https://global.ds.lsapp.eu/odds/pq_graphql").strip()
ODDS_HASH = "oce"
DEFAULT_PROJECT_ID = 46
PARTICIPANTS_RE = re.compile(r'"participantsData":(\{.*?\}),"eventParticipantEncodedId"', re.S)
PROJECT_ID_RE = re.compile(r'"project":\{"id":(\d+)', re.S)


def enabled() -> bool:
    return (os.getenv("FLASHSCORE_ODDS_ENABLED") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _headers(referer: str, *, json_response: bool = False) -> Dict[str, str]:
    headers = {
        "Accept": "application/json,*/*" if json_response else "text/html,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
        "Referer": referer,
        "User-Agent": random.choice(ss.UAS),
    }
    if json_response:
        headers["Origin"] = ss.FLASHSCORE_BASE
    return headers


def _match_id(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    return str(raw.get("flashscore_id") or event.get("custom_id") or "").strip()


def _project_id(page: str) -> int:
    configured = (os.getenv("FLASHSCORE_PROJECT_ID") or "").strip()
    if configured.isdigit():
        return int(configured)
    match = PROJECT_ID_RE.search(page or "")
    if match:
        return int(match.group(1))
    return DEFAULT_PROJECT_ID


def _participants(page: str) -> tuple[str, str]:
    match = PARTICIPANTS_RE.search(page or "")
    if not match:
        return "", ""
    try:
        data = json.loads(match.group(1))
    except Exception:
        return "", ""
    home = (data.get("home") or [{}])[0] or {}
    away = (data.get("away") or [{}])[0] or {}
    return str(home.get("eventParticipantId") or ""), str(away.get("eventParticipantId") or "")


def _float(value: Any) -> Optional[float]:
    try:
        number = float(str(value).replace(",", "."))
    except Exception:
        return None
    if number <= 1:
        return None
    return number


def _bookmaker_names(settings: Dict[str, Any]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for item in settings.get("bookmakers") or []:
        bookmaker = item.get("bookmaker") or {}
        try:
            bookmaker_id = int(bookmaker.get("id"))
        except Exception:
            continue
        out[bookmaker_id] = str(bookmaker.get("name") or bookmaker_id)
    return out


def _full_time_home_away_pairs(root: Dict[str, Any], home_ep: str, away_ep: str) -> List[Dict[str, Any]]:
    bookmaker_names = _bookmaker_names(root.get("settings") or {})
    pairs: List[Dict[str, Any]] = []
    for market in root.get("odds") or []:
        if market.get("bettingType") != "HOME_AWAY" or market.get("bettingScope") != "FULL_TIME":
            continue
        items = list(market.get("odds") or [])
        by_participant = {str(item.get("eventParticipantId") or ""): item for item in items}
        home_item = by_participant.get(home_ep) if home_ep else None
        away_item = by_participant.get(away_ep) if away_ep else None
        if (not home_item or not away_item) and len(items) == 2:
            home_item, away_item = items[0], items[1]
        if not home_item or not away_item:
            continue
        home_odds = _float(home_item.get("value"))
        away_odds = _float(away_item.get("value"))
        if home_odds is None or away_odds is None:
            continue
        try:
            bookmaker_id = int(market.get("bookmakerId"))
        except Exception:
            bookmaker_id = 0
        pairs.append(
            {
                "bookmaker_id": bookmaker_id,
                "bookmaker_name": bookmaker_names.get(bookmaker_id) or str(bookmaker_id or "unknown"),
                "home_odds": home_odds,
                "away_odds": away_odds,
                "active": bool(home_item.get("active")) and bool(away_item.get("active")),
            }
        )
    return pairs


async def _fetch_match_odds(client: httpx.AsyncClient, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    match_id = _match_id(event)
    if not match_id or ((event.get("raw") or {}).get("source") != "flashscore"):
        return None

    page_url = f"{ss.FLASHSCORE_BASE}/match/{match_id}/#/odds-comparison/1x2-odds/full-time"
    page_response = await client.get(page_url, headers=_headers(page_url))
    if page_response.status_code == 403:
        return None
    page_response.raise_for_status()
    page = page_response.text
    home_ep, away_ep = _participants(page)

    params = {
        "_hash": ODDS_HASH,
        "eventId": match_id,
        "projectId": str(_project_id(page)),
        "geoIpCode": (os.getenv("FLASHSCORE_GEOIP_CODE") or "").strip(),
        "geoIpSubdivisionCode": (os.getenv("FLASHSCORE_GEOIP_SUBDIVISION_CODE") or "").strip(),
    }
    odds_response = await client.get(ODDS_URL, params=params, headers=_headers(page_url, json_response=True))
    if odds_response.status_code == 403:
        return None
    odds_response.raise_for_status()
    data = odds_response.json()
    root = ((data.get("data") or {}).get("findOddsByEventId") or {})
    if not root:
        return None

    pairs = _full_time_home_away_pairs(root, home_ep, away_ep)
    selected = [pair for pair in pairs if pair.get("active")] or pairs
    if not selected:
        return None

    home_odds = float(statistics.median(pair["home_odds"] for pair in selected))
    away_odds = float(statistics.median(pair["away_odds"] for pair in selected))
    return {
        "home_odds": home_odds,
        "away_odds": away_odds,
        "source": "flashscore",
        "raw": {
            "match_id": match_id,
            "home_event_participant_id": home_ep,
            "away_event_participant_id": away_ep,
            "pairs": selected,
            "pairs_count": len(pairs),
        },
    }


async def odds_for_events(events: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    if not enabled() or not events:
        return {}
    limit = max(1, int(os.getenv("FLASHSCORE_ODDS_CONCURRENCY") or "4"))
    timeout = httpx.Timeout(float(os.getenv("FLASHSCORE_ODDS_TIMEOUT") or "20"))
    semaphore = asyncio.Semaphore(limit)
    out: Dict[int, Dict[str, Any]] = {}

    async with httpx.AsyncClient(http2=False, timeout=timeout, follow_redirects=True) as client:
        async def one(event: Dict[str, Any]) -> None:
            async with semaphore:
                try:
                    odds = await _fetch_match_odds(client, event)
                except Exception as exc:
                    print(f"[flashscore_odds] fetch failed event_id={event.get('event_id')}: {exc}")
                    return
                if odds and event.get("event_id"):
                    out[int(event["event_id"])] = odds

        await asyncio.gather(*(one(event) for event in events))
    return out
