# добавьте в конец файла (или замените файл целиком на этот фрагмент)

import httpx, re
from datetime import date
from typing import Dict, Any, List, Optional
from unicodedata import normalize as uni_norm
from config import HTTP_TIMEOUT

BASE = "https://api.sofascore.com/api/v1"

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", uni_norm("NFKC", s or "").strip().lower())

async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    r = await client.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 TennisBot/1.0"})
    r.raise_for_status()
    return r.json()

async def events_by_date(client: httpx.AsyncClient, day: date) -> List[Dict[str, Any]]:
    ds = day.isoformat()
    data = await _get_json(client, f"{BASE}/sport/tennis/scheduled-events/{ds}")
    return data.get("events", [])

def is_finished(event: Dict[str, Any]) -> bool:
    st = (event.get("status", {}) or {}).get("type")
    return str(st).lower() == "finished"

def event_id_of(event: Dict[str, Any]) -> Optional[str]:
    eid = event.get("id")
    return str(eid) if eid is not None else None

# ------------ NEW: helpers for tournaments / filters -------------
def _is_doubles(event: Dict[str, Any]) -> bool:
    hn = (event.get("homeTeam") or {}).get("name") or ""
    an = (event.get("awayTeam") or {}).get("name") or ""
    return (" / " in hn) and (" / " in an)

def _tour_key(event: Dict[str, Any]) -> str:
    t = event.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return str(ut.get("id") or t.get("id") or "0")

def _tour_name(event: Dict[str, Any]) -> str:
    t = event.get("tournament") or {}
    cat = (t.get("category") or {}).get("name") or ""      # ATP / WTA / Challenger Men ...
    tname = t.get("name") or (t.get("uniqueTournament") or {}).get("name") or "Tournament"
    disc = "ПАРНЫЙ РАЗРЯД" if _is_doubles(event) else "ОДИНОЧНЫЙ РАЗРЯД"
    # Лёгкий ру-лейбл для категории
    cat_ru = (cat
              .replace("Challenger Men", "ЧЕЛЛЕНДЖЕР МУЖЧИНЫ")
              .replace("Challenger Women", "ЧЕЛЛЕНДЖЕР ЖЕНЩИНЫ")
              .replace("ATP", "ATP")
              .replace("WTA", "WTA")
              .replace("Davis Cup", "Кубок Дэвиса")
              .replace("Billie Jean King Cup", "Кубок Билли Джин Кинг"))
    return f"{cat_ru} - {disc}: {tname}"

def _is_low_itf(event: Dict[str, Any]) -> bool:
    """Отсекаем ITF 15/25/50."""
    t = event.get("tournament") or {}
    raw = " ".join([
        (t.get("name") or ""),
        ((t.get("uniqueTournament") or {}).get("name") or ""),
        ((t.get("category") or {}).get("name") or "")
    ]).lower()
    if "itf" not in raw:
        return False
    return any(x in raw for x in [" 15", "m15", "w15", " 25", "m25", "w25", " 50", "w50"])

def group_tournaments(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Группируем события по турнирам, фильтруя ITF 15/25/50."""
    out: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if _is_low_itf(ev):
            continue
        key = _tour_key(ev)
        name = _tour_name(ev)
        out.setdefault(key, {"id": key, "name": name, "events": []})
        out[key]["events"].append(ev)
    # Стабильная сортировка по имени
    return sorted(out.values(), key=lambda x: x["name"].lower())

async def event_statistics(client: httpx.AsyncClient, event_id: int) -> Dict[str, Any]:
    details = await _get_json(client, f"{BASE}/event/{event_id}")
    event = details.get("event") or {}

    # --- sets ---
    def _extract_sets(ev: Dict[str, Any]) -> List[str]:
        sets = []
        hs = ev.get("homeScore", {}) or {}
        as_ = ev.get("awayScore", {}) or {}
        for i in range(1, 5+1):
            h = hs.get(f"period{i}")
            a = as_.get(f"period{i}")
            if h is None or a is None:
                continue
            sets.append(f"{h}:{a}")
        return sets or ([f"{hs.get('current','?')}:{as_.get('current','?')}"] if hs.get("current") is not None and as_.get("current") is not None else [])

    sets = _extract_sets(event)

    # --- duration ---
    duration = None
    try:
        inc = await _get_json(client, f"{BASE}/event/{event_id}/incidents")
        length = (inc.get("length") or details.get("event", {}).get("length"))
        if isinstance(length, int) and length > 0:
            h = length // 60
            m = length % 60
            duration = f"{h}:{m:02d}" if h else f"{m} мин"
    except httpx.HTTPError:
        pass

    # --- stats ---
    stats = {}
    try:
        st = await _get_json(client, f"{BASE}/event/{event_id}/statistics")
        for root in st.get("statistics", []):
            if root.get("period") != "ALL":
                continue
            for g in root.get("groups", []):
                for item in g.get("statisticsItems", []):
                    name = (item.get("name") or "").lower()
                    h = item.get("home")
                    a = item.get("away")
                    key = None
                    if "ace" in name: key = "aces"
                    elif "double" in name: key = "doubles"
                    elif "first serve in" in name or "1st serve in" in name: key = "first_serve_in_pct"
                    elif "first serve points won" in name or "1st serve points won" in name: key = "first_serve_points_won_pct"
                    elif "second serve points won" in name or "2nd serve points won" in name: key = "second_serve_points_won_pct"
                    elif "winners" in name: key = "winners"
                    elif "unforced errors" in name: key = "unforced"
                    elif "break points saved" in name: key = "break_points_saved"
                    elif "break points faced" in name: key = "break_points_faced"
                    elif "match points saved" in name: key = "match_points_saved"
                    if not key: 
                        continue
                    stats.setdefault(key, {"home": None, "away": None})
                    stats[key]["home"] = h
                    stats[key]["away"] = a
    except httpx.HTTPError:
        pass

    def pack(side: str) -> dict:
        def get_num(k): return stats.get(k, {}).get(side)
        def get_pct(k):
            v = stats.get(k, {}).get(side)
            if v is None: return None
            try:
                if isinstance(v, str) and v.endswith('%'): v = v[:-1]
                return float(v)
            except Exception:
                return None
        return {
            "aces": get_num("aces"),
            "doubles": get_num("doubles"),
            "first_serve_in_pct": get_pct("first_serve_in_pct"),
            "first_serve_points_won_pct": get_pct("first_serve_points_won_pct"),
            "second_serve_points_won_pct": get_pct("second_serve_points_won_pct"),
            "winners": get_num("winners"),
            "unforced": get_num("unforced"),
            "break_points_saved": get_num("break_points_saved"),
            "break_points_faced": get_num("break_points_faced"),
            "match_points_saved": get_num("match_points_saved"),
        }

    home_name = (event.get("homeTeam") or {}).get("name", "Игрок A")
    away_name = (event.get("awayTeam") or {}).get("name", "Игрок B")

    return {
        "event_id": str(event.get("id")),
        "home_name": home_name,
        "away_name": away_name,
        "score_sets": sets,
        "duration": duration,
        "home_stats": pack("home"),
        "away_stats": pack("away"),
    }

async def find_player_events_today(client: httpx.AsyncClient, day: date, player_queries: List[str]) -> List[Dict[str, Any]]:
    events = await events_by_date(client, day)
    return [e for e in events if any(_norm(q) in _norm((e.get("homeTeam") or {}).get("name","")) or _norm(q) in _norm((e.get("awayTeam") or {}).get("name","")) for q in player_queries)]
