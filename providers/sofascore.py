# providers/sofascore.py
from __future__ import annotations
from datetime import date
from typing import Dict, Any, List, Iterable

import httpx

# Пытаемся API по двум хостам, иногда один из них даёт 403-challenge
BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

# Браузерные заголовки
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "sec-ch-ua": '"Chromium";v="121", "Not A(Brand";v="99", "Google Chrome";v="121"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Connection": "keep-alive",
}

async def _get_json_multi(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for base in BASES:
        url = f"{base}{path}"
        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_exc = e
            continue
    assert last_exc is not None
    raise last_exc

def event_id_of(ev: Dict[str, Any]) -> str:
    return str(ev.get("id") or ev.get("event", {}).get("id") or "")

def _allowed_event(ev: Dict[str, Any]) -> bool:
    t = (ev.get("tournament") or {})
    ut = (ev.get("uniqueTournament") or t.get("uniqueTournament") or {})
    title_parts = [
        str(t.get("name") or ""),
        str(ut.get("name") or ""),
        str(ut.get("slug") or ""),
        str(t.get("slug") or ""),
        str(t.get("category", {}).get("name") or ""),
    ]
    name = " ".join(title_parts).lower()

    # Вырезаем ИТФ и юниоров
    banned = ["itf", "15k", "25k", "50k", "itf 15", "itf 25", "itf 50", "junior"]
    if any(tok in name for tok in banned):
        return False
    return True

def group_tournaments(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        t = (ev.get("tournament") or {})
        ut = (ev.get("uniqueTournament") or t.get("uniqueTournament") or {})
        key = str(ut.get("id") or t.get("id") or event_id_of(ev))
        title = (
            ut.get("name") or t.get("name") or
            f"{(ev.get('homeTeam') or {}).get('name','')} — {(ev.get('awayTeam') or {}).get('name','')}"
        )
        if key not in by_key:
            by_key[key] = {"id": key, "name": title, "events": []}
        by_key[key]["events"].append(ev)

    out = []
    for t in by_key.values():
        filtered = [ev for ev in t["events"] if _allowed_event(ev)]
        if filtered:
            t["events"] = filtered
            out.append(t)
    out.sort(key=lambda x: x["name"])
    return out

async def events_by_date(client: httpx.AsyncClient, d: date) -> List[Dict[str, Any]]:
    ds = d.isoformat()
    path = f"/sport/tennis/scheduled-events/{ds}"
    try:
        data = await _get_json_multi(client, path)
        events = data.get("events", []) or []
        return [ev for ev in events if _allowed_event(ev)]
    except httpx.HTTPStatusError as e:
        # Если оба хоста вернули 403 — попробуем отдать хотя бы live-ивенты,
        # чтобы у пользователя было меню, а не ошибка.
        if e.response is not None and e.response.status_code == 403:
            try:
                live = await _get_json_multi(client, "/sport/tennis/events/live")
                events = live.get("events", []) or []
                return [ev for ev in events if _allowed_event(ev)]
            except Exception:
                raise
        raise
