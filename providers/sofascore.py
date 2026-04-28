from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import random
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx

TOUR_LABELS = {"men": "Мужской тур", "women": "Женский тур"}

FLASHSCORE_BASE = (os.getenv("FLASHSCORE_BASE") or "https://www.flashscorekz.com").rstrip("/")
FLASHSCORE_LANG = os.getenv("FLASHSCORE_LANG", "ru").strip() or "ru"
FLASHSCORE_HOME = f"{FLASHSCORE_BASE}/tennis/"
FLASHSCORE_FSIGN = "SW9D1eZo"

GRAND_SLAM_TOURNAMENTS = (
    "australian open",
    "open australia",
    "открытый чемпионат австралии",
    "австралия open",
    "roland garros",
    "french open",
    "ролан гаррос",
    "wimbledon",
    "уимблдон",
    "us open",
    "открытый чемпионат сша",
)

COMMON_1000_TOURNAMENTS = (
    "indian wells",
    "индиан-уэллс",
    "индиан уэллс",
    "miami",
    "майами",
    "madrid",
    "мадрид",
    "rome",
    "рим",
    "canada",
    "canadian open",
    "toronto",
    "торонто",
    "montreal",
    "монреаль",
    "cincinnati",
    "цинциннати",
)

ATP_1000_TOURNAMENTS = (
    "monte carlo",
    "монте-карло",
    "монте карло",
    "shanghai",
    "шанхай",
    "paris",
    "париж",
)

WTA_1000_TOURNAMENTS = (
    "doha",
    "доха",
    "dubai",
    "дубай",
    "beijing",
    "пекин",
    "wuhan",
    "ухань",
)

ATP_500_TOURNAMENTS = (
    "rotterdam",
    "роттердам",
    "doha",
    "доха",
    "dubai",
    "дубай",
    "rio de janeiro",
    "рио-де-жанейро",
    "acapulco",
    "акапулько",
    "barcelona",
    "барселона",
    "queens",
    "queen's",
    "лондон",
    "halle",
    "халле",
    "washington",
    "вашингтон",
    "beijing",
    "пекин",
    "tokyo",
    "токио",
    "basel",
    "базель",
    "vienna",
    "вена",
    "hamburg",
    "гамбург",
    "dallas",
    "даллас",
)

WTA_500_TOURNAMENTS = (
    "brisbane",
    "брисбен",
    "adelaide",
    "аделаида",
    "abu dhabi",
    "абу-даби",
    "linz",
    "линц",
    "stuttgart",
    "штутгарт",
    "charleston",
    "чарльстон",
    "strasbourg",
    "страсбург",
    "berlin",
    "берлин",
    "bad homburg",
    "бад-хомбург",
    "eastbourne",
    "истборн",
    "seoul",
    "сеул",
    "ningbo",
    "нинбо",
    "tokyo",
    "токио",
)

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
]


def _app_today() -> dt.date:
    try:
        return dt.datetime.now(ZoneInfo(os.getenv("APP_TZ") or "Europe/Helsinki")).date()
    except Exception:
        return dt.date.today()


async def _fetch_text(url: str, extra: Optional[Dict[str, str]] = None) -> Optional[str]:
    headers = {
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
        "Connection": "keep-alive",
        "Referer": FLASHSCORE_HOME,
        "User-Agent": random.choice(UAS),
        "x-fsign": FLASHSCORE_FSIGN,
    }
    if extra:
        headers.update(extra)
    async with httpx.AsyncClient(http2=False, timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 403:
            return None
        r.raise_for_status()
        return r.text


def _fields(record: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in (record or "").split("¬"):
        if "÷" in part:
            key, value = part.split("÷", 1)
            out[key] = value
    return out


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _lower(*parts: Any) -> str:
    return " ".join(str(p or "").strip().lower() for p in parts if p is not None).strip()


def _ranked_status(category: str, tournament: str, season: str) -> tuple[str, int]:
    category = (category or "Other").strip()
    hay = _lower(category, tournament, season).replace("ё", "е")
    if category == "ITF" or "itf" in hay or any(x in hay for x in ("m15", "m25", "m35", "m50", "w15", "w25", "w35", "w50", "w75", "w100")):
        marker = ""
        for token in ("M15", "M25", "M35", "M50", "W15", "W25", "W35", "W50", "W75", "W100"):
            if token.lower() in hay:
                marker = f" {token}"
                break
        return f"ITF{marker}", 5
    if category == "Challenger" or "challenger" in hay or "челленджер" in hay:
        return "Challenger", 4
    if any(x in hay for x in GRAND_SLAM_TOURNAMENTS):
        return "Grand Slam", 0
    if category in {"ATP", "WTA"} and "1000" in hay:
        return f"{category} 1000", 1
    if category in {"ATP", "WTA"} and "500" in hay:
        return f"{category} 500", 2
    if category in {"ATP", "WTA"} and "250" in hay:
        return f"{category} 250", 3
    if category == "ATP" and any(x in hay for x in COMMON_1000_TOURNAMENTS + ATP_1000_TOURNAMENTS):
        return "ATP 1000", 1
    if category == "WTA" and any(x in hay for x in COMMON_1000_TOURNAMENTS + WTA_1000_TOURNAMENTS):
        return "WTA 1000", 1
    if any(x in hay for x in COMMON_1000_TOURNAMENTS):
        prefix = "WTA" if category == "WTA" else "ATP" if category == "ATP" else category
        return f"{prefix} 1000".strip(), 1
    if category == "ATP":
        if any(x in hay for x in ATP_500_TOURNAMENTS):
            return "ATP 500", 2
        return "ATP 250", 3
    if category == "WTA":
        if any(x in hay for x in WTA_500_TOURNAMENTS):
            return "WTA 500", 2
        return "WTA 250", 3
    return category or "Other", 6


def _stable_id(source: str, raw_id: Any) -> int:
    digest = hashlib.md5(f"{source}:{raw_id or ''}".encode("utf-8")).hexdigest()
    return 700_000_000 + (int(digest[:9], 16) % 200_000_000)


def _league_category(league: str) -> str:
    upper = (league or "").upper()
    if "ITF" in upper:
        return "ITF"
    if "CHALLENGER" in upper or "ЧЕЛЛЕНДЖЕР" in upper:
        return "Challenger"
    if "WTA" in upper:
        return "WTA"
    if "ATP" in upper:
        return "ATP"
    return "Other"


def _league_group(league: str) -> str:
    upper = (league or "").upper()
    if (
        "WTA" in upper
        or "WOMEN" in upper
        or "ЖЕНЩИН" in upper
        or any(x in upper for x in ("W15", "W25", "W35", "W50", "W75", "W100"))
    ):
        return "women"
    if (
        "ATP" in upper
        or "MEN" in upper
        or "МУЖЧИН" in upper
        or "CHALLENGER" in upper
        or "ЧЕЛЛЕНДЖЕР" in upper
        or any(x in upper for x in ("M15", "M25", "M35", "M50"))
    ):
        return "men"
    return "other"


def _tournament_from_league(league: str) -> str:
    raw = _clean(league)
    if ":" not in raw:
        return raw or "Tournament"
    rest = raw.split(":", 1)[1].strip()
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    surfaces = {"clay", "hard", "grass", "indoor hard", "грунт", "хард", "трава", "зал"}
    if len(parts) > 1 and parts[-1].lower() in surfaces:
        rest = ", ".join(parts[:-1])
    return rest or raw


def _num(value: Optional[str]) -> Optional[Any]:
    if value in (None, ""):
        return None
    try:
        n = float(str(value))
        return int(n) if n.is_integer() else n
    except Exception:
        return value


def _score(fields: Dict[str, str], side: str) -> Dict[str, Any]:
    total_key = "AG" if side == "home" else "AH"
    period_keys = ["BA", "BC", "BE", "BG", "BI"] if side == "home" else ["BB", "BD", "BF", "BH", "BJ"]
    tiebreak_keys = ["DA", "DC", "DE", "DG", "DI"] if side == "home" else ["DB", "DD", "DF", "DH", "DJ"]
    out: Dict[str, Any] = {}

    total = _num(fields.get(total_key))
    if total is not None:
        out["current"] = total
        out["display"] = total

    for idx, key in enumerate(period_keys, start=1):
        value = _num(fields.get(key))
        if value is not None:
            out[f"period{idx}"] = value
    for idx, key in enumerate(tiebreak_keys, start=1):
        value = _num(fields.get(key))
        if value is not None:
            out[f"period{idx}TieBreak"] = value
    return out


def _status(fields: Dict[str, str]) -> str:
    phase = fields.get("AB") or ""
    detail = fields.get("AC") or ""
    note = _lower(fields.get("AM"))
    if phase == "1":
        return "notstarted"
    if phase == "2":
        return "inprogress"
    if detail == "5" or "withdrawn" in note or "cancelled" in note:
        return "cancelled"
    if detail == "8" or "retired" in note:
        return "retired"
    if phase == "3":
        return "finished"
    return "unknown"


def _winner_code(fields: Dict[str, str]) -> Optional[int]:
    home = _num(fields.get("AG"))
    away = _num(fields.get("AH"))
    if isinstance(home, (int, float)) and isinstance(away, (int, float)):
        if home > away:
            return 1
        if away > home:
            return 2
    return None


def _flashscore_event(fields: Dict[str, str], league: Dict[str, str]) -> Optional[Dict[str, Any]]:
    match_id = fields.get("AA")
    if not match_id:
        return None

    league_name = league.get("ZA") or league.get("ZAF") or ""
    category = _league_category(league_name)
    tournament = _tournament_from_league(league_name)
    event: Dict[str, Any] = {
        "id": _stable_id("flashscore", match_id),
        "customId": match_id,
        "tournament": {
            "name": tournament,
            "uniqueTournament": {
                "name": tournament,
                "category": {"name": category, "slug": category.lower()},
            },
            "category": {"name": category, "slug": category.lower()},
        },
        "season": {"name": league.get("ZAF") or league_name},
        "homeCompetitor": {"name": _clean(fields.get("AE") or "TBD")},
        "awayCompetitor": {"name": _clean(fields.get("AF") or "TBD")},
        "startTimestamp": int(fields["AD"]) if str(fields.get("AD") or "").isdigit() else None,
        "status": {"type": _status(fields), "detail": fields.get("AM") or ""},
        "homeScore": _score(fields, "home"),
        "awayScore": _score(fields, "away"),
        "source": "flashscore",
        "flashscore_id": match_id,
        "flashscore_league": league_name,
        "tour_group_hint": _league_group(league_name),
    }
    winner = _winner_code(fields)
    if winner:
        event["winnerCode"] = winner
    return event


async def flashscore_events_by_date(day: dt.date) -> Dict[str, Any]:
    offset = (day - _app_today()).days
    text = await _fetch_text(f"{FLASHSCORE_BASE}/x/feed/f_2_{offset}_2_{FLASHSCORE_LANG}_1")
    if not text or text == "0":
        return {"source": "flashscore", "events": []}

    league: Dict[str, str] = {}
    events: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for record in text.split("¬~"):
        fields = _fields(record)
        if not fields:
            continue
        if "ZA" in fields:
            league = fields
            continue
        if "AA" not in fields:
            continue
        event = _flashscore_event(fields, league)
        if not event:
            continue
        event_id = int(event["id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        events.append(event)
    return {"source": "flashscore", "events": events}


async def events_by_date(day: dt.date) -> Dict[str, Any]:
    return await flashscore_events_by_date(day)


def _category_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    cat = ut.get("category") or t.get("category") or {}
    return _lower(cat.get("name"), cat.get("slug"))


def _tournament_name(ev: Dict[str, Any]) -> str:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    return (ut.get("name") or t.get("name") or "").strip()


def _season_name(ev: Dict[str, Any]) -> str:
    return ((ev.get("season") or {}).get("name") or "").strip()


def classify(ev: Dict[str, Any]) -> str:
    hay = f"{_category_name(ev)} {_tournament_name(ev)} {_season_name(ev)}".lower()
    if any(x in hay for x in ("itf", "m15", "m25", "m35", "m50", "w15", "w25", "w35", "w50", "w75", "w100")):
        return "ITF"
    if "challenger" in hay or "челленджер" in hay:
        return "Challenger"
    if "wta" in hay or "women" in hay or "female" in hay or "женщин" in hay:
        return "WTA"
    if "atp" in hay or "men" in hay or "male" in hay or "мужчин" in hay:
        return "ATP"
    return "Other"


def tour_group(ev: Dict[str, Any]) -> str:
    raw_hint = ev.get("tour_group_hint")
    if raw_hint in {"men", "women"}:
        return raw_hint
    category = classify(ev)
    hay = f"{_category_name(ev)} {_tournament_name(ev)} {_season_name(ev)}".lower()
    if category == "WTA" or any(x in hay for x in ("wta", "women", "female", "женщин", "w15", "w25", "w35", "w50", "w75", "w100")):
        return "women"
    if category in {"ATP", "Challenger"} or any(x in hay for x in ("atp", "challenger", "челленджер", "men", "male", "мужчин", "m15", "m25", "m35", "m50")):
        return "men"
    return "other"


def tour_label(group: str) -> str:
    return TOUR_LABELS.get(group, "Другой тур")


def _side_name(ev: Dict[str, Any], side: str) -> str:
    keys = ["homePlayer", "homeCompetitor", "homeTeam", "home"] if side == "home" else ["awayPlayer", "awayCompetitor", "awayTeam", "away"]
    for key in keys:
        obj = ev.get(key)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("shortName")
            if name:
                return str(name)
    return "TBD"


def normalize_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    group = tour_group(ev)
    category = classify(ev)
    tournament = _tournament_name(ev)
    season = _season_name(ev)
    tournament_status, tournament_rank = _ranked_status(category, tournament, season)
    return {
        "event_id": int(ev.get("id")),
        "custom_id": ev.get("customId"),
        "tournament_name": tournament,
        "season_name": season,
        "category": category,
        "tournament_status": tournament_status,
        "tournament_sort_rank": tournament_rank,
        "tour_group": group,
        "tour_label": tour_label(group),
        "home_name": _side_name(ev, "home"),
        "away_name": _side_name(ev, "away"),
        "start_ts": ev.get("startTimestamp") if isinstance(ev.get("startTimestamp"), int) else None,
        "status_type": ((ev.get("status") or {}).get("type") or "").lower(),
        "raw": ev,
    }


def normalize_events(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ev in data.get("events", []) or []:
        try:
            if ev.get("id"):
                rows.append(normalize_event(ev))
        except Exception:
            continue
    return rows


def filter_by_tour_group(events: List[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("tour_group") == group]


def filter_by_category(events: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("category") == category]


def tournaments_for_tour_group(events: List[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = {}
    for event in filter_by_tour_group(events, group):
        key = event["tournament_name"]
        row = bucket.setdefault(
            key,
            {
                "tournament_name": key,
                "tour_group": event["tour_group"],
                "tour_label": event["tour_label"],
                "tournament_status": event.get("tournament_status") or event.get("category") or "Other",
                "tournament_sort_rank": int(event.get("tournament_sort_rank", 6)),
                "matches_count": 0,
                "live_count": 0,
                "finished_count": 0,
            },
        )
        rank = int(event.get("tournament_sort_rank", 6))
        if rank < int(row.get("tournament_sort_rank", 6)):
            row["tournament_sort_rank"] = rank
            row["tournament_status"] = event.get("tournament_status") or row.get("tournament_status") or "Other"
        row["matches_count"] += 1
        status = status_type(event)
        if status == "inprogress":
            row["live_count"] += 1
        if status in {"finished", "retired", "cancelled", "walkover"}:
            row["finished_count"] += 1
    return sorted(bucket.values(), key=lambda x: (int(x.get("tournament_sort_rank", 6)), x["tournament_name"].lower(), -int(x["matches_count"])))


def tournaments_for_category(events: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
    bucket: Dict[str, Dict[str, Any]] = {}
    for event in filter_by_category(events, category):
        key = event["tournament_name"]
        row = bucket.setdefault(key, {"tournament_name": key, "category": category, "matches_count": 0})
        row["matches_count"] += 1
    return sorted(bucket.values(), key=lambda x: (x["tournament_name"].lower(), x["matches_count"]))


def matches_for_tournament_in_tour(events: List[Dict[str, Any]], group: str, tournament: str) -> List[Dict[str, Any]]:
    rows = [e for e in filter_by_tour_group(events, group) if e.get("tournament_name") == tournament]
    rows.sort(key=lambda x: (x.get("start_ts") or 0, x.get("home_name") or "", x.get("away_name") or ""))
    return rows


def matches_for_tournament(events: List[Dict[str, Any]], category: str, tournament: str) -> List[Dict[str, Any]]:
    rows = [e for e in filter_by_category(events, category) if e.get("tournament_name") == tournament]
    rows.sort(key=lambda x: (x.get("start_ts") or 0, x.get("home_name") or "", x.get("away_name") or ""))
    return rows


def status_type(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    return (event.get("status_type") or ((raw.get("status") or {}).get("type") or "")).lower()


def status_label(event: Dict[str, Any]) -> str:
    status = status_type(event)
    return {
        "finished": "Завершен",
        "retired": "Завершен (снятие)",
        "cancelled": "Отменен",
        "walkover": "Тех. победа",
        "inprogress": "Идет",
        "notstarted": "Не начался",
    }.get(status, "Статус неизвестен")


def is_finished(event: Dict[str, Any]) -> bool:
    return status_type(event) in {"finished", "retired", "cancelled", "walkover"}


def _score_obj(event: Dict[str, Any], side: str) -> Dict[str, Any]:
    raw = event.get("raw") or {}
    key = "homeScore" if side == "home" else "awayScore"
    obj = raw.get(key) or {}
    return obj if isinstance(obj, dict) else {}


def _score_value(score: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        if score.get(key) is not None:
            return score.get(key)
    return None


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def final_score(event: Dict[str, Any]) -> str:
    home = _score_value(_score_obj(event, "home"), "current", "display")
    away = _score_value(_score_obj(event, "away"), "current", "display")
    if home is None or away is None:
        return ""
    return f"{_fmt(home)}-{_fmt(away)}"


def set_scores(event: Dict[str, Any]) -> List[str]:
    home = _score_obj(event, "home")
    away = _score_obj(event, "away")
    out: List[str] = []
    for idx in range(1, 6):
        h = _score_value(home, f"period{idx}")
        a = _score_value(away, f"period{idx}")
        if h is None or a is None:
            continue
        item = f"{_fmt(h)}-{_fmt(a)}"
        ht = _score_value(home, f"period{idx}TieBreak")
        at = _score_value(away, f"period{idx}TieBreak")
        if ht not in (None, 0, "0") or at not in (None, 0, "0"):
            item += f" ({_fmt(ht or 0)}-{_fmt(at or 0)})"
        out.append(item)
    return out


def compact_score(event: Dict[str, Any]) -> str:
    total = final_score(event)
    sets = set_scores(event)
    if total and sets:
        return f"{total} ({', '.join(sets)})"
    if total:
        return total
    if sets:
        return f"({', '.join(sets)})"
    return ""


async def _match_feed(match_id: str, feed: str) -> Optional[str]:
    return await _fetch_text(
        f"{FLASHSCORE_BASE}/x/feed/{feed}_{match_id}",
        {"Referer": f"{FLASHSCORE_BASE}/match/{match_id}/"},
    )


def _parse_stats(text: Optional[str]) -> Dict[str, Dict[str, str]]:
    stats: Dict[str, Dict[str, str]] = {}
    scope = ""
    for record in (text or "").split("¬~"):
        fields = _fields(record)
        if fields.get("SE"):
            scope = fields["SE"]
        if scope.lower() in {"match", "матч"} and fields.get("SG"):
            stats[fields["SG"]] = {"home": fields.get("SH") or "", "away": fields.get("SI") or ""}
    return stats


def _parse_summary(text: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"sets": []}
    for record in (text or "").split("¬~"):
        fields = _fields(record)
        if fields.get("AC"):
            out["sets"].append({"name": fields.get("AC"), "duration": fields.get("RC") or fields.get("RD")})
        if fields.get("RB"):
            out["duration"] = fields["RB"]
    return out


async def enrich_event(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("raw") or {}
    match_id = raw.get("flashscore_id") or event.get("custom_id")
    if raw.get("source") != "flashscore" or not match_id:
        return event
    try:
        stats_text, summary_text = await asyncio.gather(
            _match_feed(str(match_id), "df_st_2"),
            _match_feed(str(match_id), "df_sui_2"),
        )
        raw["flashscore_stats"] = _parse_stats(stats_text)
        raw["flashscore_summary"] = _parse_summary(summary_text)
        event["raw"] = raw
    except Exception as exc:
        raw["flashscore_stats_error"] = str(exc)
        event["raw"] = raw
    return event


def _norm_stat_name(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _stat_pair(stats: Dict[str, Dict[str, str]], *names: str) -> Optional[str]:
    row: Dict[str, str] = {}
    for name in names:
        row = stats.get(name) or {}
        if row:
            break
    if not row:
        wanted = {_norm_stat_name(name) for name in names}
        for key, value in stats.items():
            if _norm_stat_name(key) in wanted:
                row = value
                break
    home, away = row.get("home"), row.get("away")
    if not home and not away:
        return None
    return f"{home or '-'} - {away or '-'}"


def _stats_lines(event: Dict[str, Any]) -> List[str]:
    raw = event.get("raw") or {}
    stats = raw.get("flashscore_stats") or {}
    if not isinstance(stats, dict) or not stats:
        return []
    lines: List[str] = []
    duration = ((raw.get("flashscore_summary") or {}).get("duration") or "").strip()
    if duration:
        lines.append(f"Длительность: {duration}")
    pairs = [
        (("Aces", "Подачи навылет", "Эйсы"), "эйсы"),
        (("Double Faults", "Двойные ошибки"), "двойные"),
        (("1st Serve Percentage", "1st serve percentage", "1-я подача", "Процент первой подачи"), "первая подача"),
        (("1st serve points won", "Очки выигр. на п.п.", "Выиграно очков на 1-й подаче"), "очки на первой подаче"),
        (("2nd serve points won", "Очки выигр. на в.п.", "Выиграно очков на 2-й подаче"), "очки на второй подаче"),
        (("Break Points Converted", "Реализованные брейкпойнты", "Брейк-пойнты"), "брейк-пойнты"),
        (("Winners", "Активно выигр. мячи", "Виннерсы"), "виннерсы"),
        (("Unforced errors", "Невынужд. ошибки"), "невынужденные"),
        (("Total Points Won", "Всего выигранных очков", "Выиграно очков"), "всего очков"),
    ]
    for keys, label in pairs:
        value = None
        for key in keys:
            value = _stat_pair(stats, key)
            if value:
                break
        if value:
            lines.append(f"{label}: {value}")
    return lines[:9]


def stats_message(event: Dict[str, Any]) -> str:
    stats = _stats_lines(event)
    if not stats:
        return ""
    return "\n".join(["Основная статистика:", *stats]).strip()


def winner_name(event: Dict[str, Any]) -> str:
    winner = (event.get("raw") or {}).get("winnerCode")
    if str(winner) == "1":
        return str(event.get("home_name") or "")
    if str(winner) == "2":
        return str(event.get("away_name") or "")
    return ""


def result_message(event: Dict[str, Any], include_stats: bool = True) -> str:
    lines = [
        status_label(event),
        f"{event.get('tour_label') or tour_label(event.get('tour_group', ''))}: {event.get('tournament_name') or 'Турнир'}",
        "",
        f"{event.get('home_name') or 'TBD'} - {event.get('away_name') or 'TBD'}",
    ]
    score = compact_score(event)
    if score:
        lines.append(f"Счет: {score}")
    note = (((event.get("raw") or {}).get("status") or {}).get("detail") or "").strip()
    if status_type(event) in {"cancelled", "retired", "walkover"} and note:
        lines.append(f"Примечание: {note}")
    winner = winner_name(event)
    if winner:
        lines.append(f"Победитель: {winner}")
    stats = _stats_lines(event) if include_stats else []
    if stats:
        lines.append("")
        lines.append("Краткая статистика:")
        lines.extend(stats)
    return "\n".join(lines).strip()
