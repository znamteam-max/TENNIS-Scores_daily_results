from __future__ import annotations

import datetime as dt
import random
import httpx
from typing import Dict, Any, List

BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}


async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    h = dict(HEADERS)
    h["User-Agent"] = random.choice(UAS)
    r = await client.get(url, headers=h, timeout=25.0)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {}


def _ds(d: dt.date) -> str:
    return d.isoformat()


async def events_by_date(client: httpx.AsyncClient, d: dt.date) -> Dict[str, Any]:
    """
    Возвращает {"events":[...]} или {}.
    Сначала пытаемся scheduled-events/<date>.
    Если 403/ошибка — fallback на events/<date>, затем live.
    """
    paths = [
        f"/sport/tennis/scheduled-events/{_ds(d)}",
        f"/sport/tennis/events/{_ds(d)}",
    ]
    last_exc = None
    for base in BASES:
        for path in paths:
            try:
                data = await _get_json(client, f"{base}{path}")
                if data:
                    return data
            except httpx.HTTPError as e:
                last_exc = e
                continue
    # запасной live (может вернуть не тот день — дальше фильтруем по дате)
    try:
        data = await _get_json(client, f"{BASES[0]}/sport/tennis/events/live")
        if data:
            return data
    except Exception:
        pass
    if last_exc:
        raise last_exc
    return {}


def pick_players(ev: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("homeTeam", "homeCompetitor", "homePlayer"):
        t = ev.get(key) or {}
        nm = t.get("name") or t.get("shortName")
        if nm: out.append(nm)
    for key in ("awayTeam", "awayCompetitor", "awayPlayer"):
        t = ev.get(key) or {}
        nm = t.get("name") or t.get("shortName")
        if nm: out.append(nm)
    # doubles safety: trim to unique
    seen = set()
    uniq: List[str] = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def pretty_tournament_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return ut.get("name") or t.get("name") or "Турнир"


def classify_tier(ev: Dict[str, Any]) -> str:
    """
    'Challengers' если в названии турнира/категории встречается 'Challenger'.
    'ATP' если встречается 'ATP' или 'Grand Slam' (всё верхнего уровня).
    Иначе 'Другие' (сюда попадут WTA, ITF и т.д.)
    """
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = (ut.get("category") or t.get("category") or {})
    texts = [
        (ut.get("name") or "").lower(),
        (t.get("name") or "").lower(),
        (cat.get("name") or "").lower(),
    ]
    txt = " ".join(texts)
    if "challenger" in txt:
        return "Challengers"
    if "atp" in txt or "grand slam" in txt:
        return "ATP"
    return "Другие"
