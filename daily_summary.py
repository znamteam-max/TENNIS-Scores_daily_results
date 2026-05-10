from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import statistics
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

from db_pg import (
    get_match_odds_map,
    is_daily_summary_sent,
    mark_daily_summary_sent,
    mark_odds_refresh,
    odds_refresh_due,
    ru_name_for,
    upsert_match_odds,
)
from match_card import FLASHSCORE_BASE, _normalize_stage
from providers import flashscore_odds, odds_api
from providers import sofascore as ss


TARGET_RANKS = {0, 1, 2, 3}
TARGET_CATEGORIES = {"ATP", "WTA"}
RUSSIAN_NAME_HINTS = {
    "медведев",
    "рублев",
    "рублёв",
    "хачанов",
    "сафиуллин",
    "каратцев",
    "котов",
    "андреев",
    "андреева",
    "шнайдер",
    "александрова",
    "касаткина",
    "самсонова",
    "кудерметова",
    "потапова",
    "павлюченкова",
    "калинская",
    "блинкова",
    "рахимова",
    "аванесян",
    "звонарева",
    "звонарёва",
}


def enabled() -> bool:
    value = os.getenv("SUMMARY_ENABLED")
    if value is not None:
        return value.strip().lower() not in {"0", "false", "no", "off"}
    source = _odds_source()
    if source in {"0", "false", "no", "off", "none"}:
        return False
    if source == "odds_api":
        return odds_api.enabled()
    return flashscore_odds.enabled()


def _odds_source() -> str:
    return (os.getenv("SUMMARY_ODDS_SOURCE") or "flashscore").strip().lower().replace("-", "_")


def _requires_odds() -> bool:
    return (os.getenv("SUMMARY_REQUIRE_ODDS") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _summary_chat_id(default_chat_id: int | str) -> int | str:
    return (os.getenv("SUMMARY_CHAT_ID") or default_chat_id)


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower().replace("ё", "е")
    return value


def _tokens(*parts: Any) -> set[str]:
    text = _norm(" ".join(str(p or "") for p in parts))
    return {x for x in re.findall(r"[a-zа-я0-9]+", text) if len(x) > 1}


def _side_sources(event: Dict[str, Any], side: str) -> List[str]:
    raw = event.get("raw") or {}
    competitor = raw.get("homeCompetitor" if side == "home" else "awayCompetitor") or {}
    return [
        str(event.get(f"{side}_name") or ""),
        str(competitor.get("name") or ""),
        str(competitor.get("shortName") or ""),
        str(competitor.get("slug") or ""),
    ]


def _side_tokens(event: Dict[str, Any], side: str) -> set[str]:
    return _tokens(*_side_sources(event, side))


def _is_same_side(side_tokens: set[str], name: Any) -> bool:
    candidate = _tokens(name)
    return bool(side_tokens and candidate and side_tokens.intersection(candidate))


def _parse_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _time_delta_seconds(event: Dict[str, Any], odds_item: Dict[str, Any]) -> int:
    start_ts = event.get("start_ts")
    odds_time = _parse_time(odds_item.get("commence_time"))
    if not start_ts or not odds_time:
        return 10**9
    event_time = dt.datetime.fromtimestamp(int(start_ts), tz=dt.timezone.utc)
    return int(abs((event_time - odds_time.astimezone(dt.timezone.utc)).total_seconds()))


def _odds_prices_for_event(event: Dict[str, Any], odds_item: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    home_tokens = _side_tokens(event, "home")
    away_tokens = _side_tokens(event, "away")
    home_prices: List[float] = []
    away_prices: List[float] = []
    for bookmaker in odds_item.get("bookmakers") or []:
        for market in bookmaker.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes") or []:
                try:
                    price = float(outcome.get("price"))
                except Exception:
                    continue
                name = outcome.get("name")
                if _is_same_side(home_tokens, name):
                    home_prices.append(price)
                elif _is_same_side(away_tokens, name):
                    away_prices.append(price)
    if not home_prices or not away_prices:
        return None, None
    return float(statistics.median(home_prices)), float(statistics.median(away_prices))


def _match_odds_item(event: Dict[str, Any], odds_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    home_tokens = _side_tokens(event, "home")
    away_tokens = _side_tokens(event, "away")
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for item in odds_items:
        if _time_delta_seconds(event, item) > 18 * 60 * 60:
            continue
        direct = _is_same_side(home_tokens, item.get("home_team")) and _is_same_side(away_tokens, item.get("away_team"))
        reverse = _is_same_side(home_tokens, item.get("away_team")) and _is_same_side(away_tokens, item.get("home_team"))
        if not direct and not reverse:
            continue
        prices = _odds_prices_for_event(event, item)
        if prices[0] is None or prices[1] is None:
            continue
        score = 10 - min(_time_delta_seconds(event, item) // 3600, 9)
        if score > best_score:
            best = item
            best_score = score
    return best


async def cache_match_odds(day: dt.date, events: List[Dict[str, Any]]) -> int:
    if _odds_source() == "odds_api":
        return await _cache_odds_api(day, events)
    return await _cache_flashscore_odds(day, events)


async def _cache_flashscore_odds(day: dt.date, events: List[Dict[str, Any]]) -> int:
    if not flashscore_odds.enabled():
        return 0
    event_ids = [int(event["event_id"]) for event in events if event.get("event_id")]
    existing = get_match_odds_map(event_ids)
    target_events = [
        event
        for event in events
        if _is_target_event(event)
        and ss.is_finished(event)
        and event.get("event_id")
        and int(event["event_id"]) not in existing
    ]
    if not target_events:
        return 0

    refresh_minutes = int(os.getenv("FLASHSCORE_ODDS_REFRESH_MINUTES") or os.getenv("ODDS_REFRESH_MINUTES") or "30")
    if not odds_refresh_due(day, refresh_minutes):
        return 0

    odds_map = await flashscore_odds.odds_for_events(target_events)
    saved = 0
    for event in target_events:
        event_id = int(event["event_id"])
        odds = odds_map.get(event_id)
        if not odds:
            continue
        upsert_match_odds(
            event_id,
            day,
            float(odds["home_odds"]),
            float(odds["away_odds"]),
            str(odds.get("source") or "flashscore"),
            odds.get("raw") or {},
        )
        saved += 1
    mark_odds_refresh(day)
    print(f"[summary] flashscore odds cached day={day} saved={saved} target_events={len(target_events)}")
    return saved


async def _cache_odds_api(day: dt.date, events: List[Dict[str, Any]]) -> int:
    if not odds_api.enabled():
        return 0
    refresh_minutes = int(os.getenv("ODDS_REFRESH_MINUTES") or "30")
    if not odds_refresh_due(day, refresh_minutes):
        return 0

    target_events = [event for event in events if _is_target_event(event) and not ss.is_finished(event)]
    if not target_events:
        mark_odds_refresh(day)
        return 0

    odds_items = await odds_api.odds_by_date(day)
    saved = 0
    for event in target_events:
        item = _match_odds_item(event, odds_items)
        if not item:
            continue
        home_odds, away_odds = _odds_prices_for_event(event, item)
        if home_odds is None or away_odds is None:
            continue
        upsert_match_odds(
            int(event["event_id"]),
            day,
            home_odds,
            away_odds,
            str(item.get("sport_key") or "the-odds-api"),
            item,
        )
        saved += 1
    mark_odds_refresh(day)
    print(f"[summary] odds cached day={day} saved={saved} source_events={len(odds_items)}")
    return saved


def _is_doubles(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = _norm(" ".join(str(x or "") for x in (raw.get("flashscore_league"), event.get("season_name"), event.get("tournament_name"))))
    return "парн" in hay or "doubles" in hay


def _is_target_event(event: Dict[str, Any]) -> bool:
    if event.get("category") not in TARGET_CATEGORIES:
        return False
    try:
        if int(event.get("tournament_sort_rank", 9)) not in TARGET_RANKS:
            return False
    except Exception:
        return False
    if _is_doubles(event):
        return False
    allow = [x.strip().lower() for x in (os.getenv("SUMMARY_TOURNAMENT_ALLOWLIST") or "").split(",") if x.strip()]
    block = [x.strip().lower() for x in (os.getenv("SUMMARY_TOURNAMENT_BLOCKLIST") or "").split(",") if x.strip()]
    name = _norm(event.get("tournament_name"))
    if allow and not any(x in name for x in allow):
        return False
    if block and any(x in name for x in block):
        return False
    return True


def _stage_from_page(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    match_id = raw.get("flashscore_id") or event.get("custom_id")
    if not match_id:
        return ""
    try:
        req = urllib.request.Request(
            f"{FLASHSCORE_BASE}/match/{match_id}/#/match-summary",
            headers={"Accept": "text/html,*/*", "Accept-Language": "ru-RU,ru;q=0.9", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            page = resp.read().decode("utf-8", "replace")
        match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', page, flags=re.I)
        if not match:
            return ""
        description = html.unescape(match.group(1))
        if " - " not in description:
            return ""
        return _normalize_stage(description.rsplit(" - ", 1)[1])
    except Exception:
        return ""


def _event_stage(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    for key in ("card_stage", "flashscore_round", "round", "stage"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_stage(value)
    return _stage_from_page(event)


def _common_stage(events: List[Dict[str, Any]]) -> str:
    stages = [stage for stage in (_event_stage(event) for event in events) if stage]
    if not stages:
        return "игровой день"
    return Counter(stages).most_common(1)[0][0]


def _side_countries(event: Dict[str, Any], side: str) -> List[str]:
    raw = event.get("raw") or {}
    competitor = raw.get("homeCompetitor" if side == "home" else "awayCompetitor") or {}
    countries = competitor.get("countries")
    if isinstance(countries, list):
        return [str(x) for x in countries if x]
    country = competitor.get("country")
    return [str(country)] if country else []


def _is_russian_side(event: Dict[str, Any], side: str) -> bool:
    countries = " ".join(_side_countries(event, side))
    if _tokens(countries).intersection({"россия", "russia", "rus"}):
        return True
    hints = set(RUSSIAN_NAME_HINTS)
    hints.update(_norm(x.strip()) for x in (os.getenv("SUMMARY_RUSSIAN_NAME_HINTS") or "").split(",") if x.strip())
    name = _norm(event.get(f"{side}_name"))
    return any(hint and hint in name for hint in hints)


def _winner_side(event: Dict[str, Any]) -> str:
    code = (event.get("raw") or {}).get("winnerCode")
    if str(code) == "1":
        return "home"
    if str(code) == "2":
        return "away"
    return ""


def _score_value(score: Dict[str, Any], key: str) -> Optional[Any]:
    value = score.get(key)
    return value if value not in (None, "") else None


def _fmt_num(value: Any) -> str:
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    except Exception:
        return str(value)


def _winner_sets(event: Dict[str, Any], winner: str) -> str:
    raw = event.get("raw") or {}
    home = raw.get("homeScore") or {}
    away = raw.get("awayScore") or {}
    parts: List[str] = []
    for idx in range(1, 6):
        h = _score_value(home, f"period{idx}")
        a = _score_value(away, f"period{idx}")
        if h is None or a is None:
            continue
        ht = _score_value(home, f"period{idx}TieBreak")
        at = _score_value(away, f"period{idx}TieBreak")
        if winner == "away":
            h, a = a, h
            ht, at = at, ht
        text = f"{_fmt_num(h)}:{_fmt_num(a)}"
        if ht not in (None, 0, "0") or at not in (None, 0, "0"):
            text += f" ({_fmt_num(ht or 0)}:{_fmt_num(at or 0)})"
        parts.append(text)
    return ", ".join(parts)


def _alias_or_name(name: str) -> str:
    try:
        alias, ok = ru_name_for(name)
        if ok and alias:
            return alias
    except Exception:
        pass
    return name


def _short_player(part: str) -> str:
    text = " ".join(str(part or "").replace("\u00a0", " ").split())
    text = re.sub(r"\s+[A-ZА-ЯЁ]\.(?:\s*-\s*[A-ZА-ЯЁ]\.)?$", "", text).strip()
    text = re.sub(r"^([A-ZА-ЯЁ])\.\s*(\S+)$", r"\1.\2", text).strip()
    return text


def _short_side(name: str) -> str:
    text = _alias_or_name(name)
    if "/" in text:
        return " / ".join(_short_player(part) for part in text.split("/") if part.strip())
    return _short_player(text)


def _result_line(event: Dict[str, Any]) -> str:
    winner = _winner_side(event)
    loser = "away" if winner == "home" else "home"
    if winner not in {"home", "away"}:
        return ""
    score = _winner_sets(event, winner)
    if not score:
        return ""
    return f"{_short_side(str(event.get(f'{winner}_name') or 'TBD'))} — {_short_side(str(event.get(f'{loser}_name') or 'TBD'))} {score}"


def _category_for(event: Dict[str, Any], odds: Optional[Dict[str, Any]]) -> str:
    winner = _winner_side(event)
    if winner not in {"home", "away"}:
        return "no_odds"
    loser = "away" if winner == "home" else "home"
    if _is_russian_side(event, loser):
        return "sad"
    if not odds or not odds.get("home_odds") or not odds.get("away_odds"):
        return "no_odds"

    home_odds = float(odds["home_odds"])
    away_odds = float(odds["away_odds"])
    if home_odds <= 1 or away_odds <= 1:
        return "no_odds"
    home_prob = (1 / home_odds) / ((1 / home_odds) + (1 / away_odds))
    away_prob = 1 - home_prob
    pickem_margin = float(os.getenv("SUMMARY_PICKEM_MARGIN") or "0.08")
    if abs(home_prob - away_prob) <= pickem_margin:
        return "pickem"
    favorite = "home" if home_odds < away_odds else "away"
    return "expected" if winner == favorite else "unexpected"


def _float_or_none(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    return number if number > 0 else None


def _fmt_odds(value: float) -> str:
    return f"{value:.2f}"


def _line_with_average_odds(event: Dict[str, Any], line: str, odds: Optional[Dict[str, Any]]) -> str:
    winner = _winner_side(event)
    if winner not in {"home", "away"} or not odds:
        return line
    home_odds = _float_or_none(odds.get("home_odds"))
    away_odds = _float_or_none(odds.get("away_odds"))
    if home_odds is None or away_odds is None:
        return line
    winner_odds, loser_odds = (home_odds, away_odds) if winner == "home" else (away_odds, home_odds)
    return f"{line} (ср. кэф. {_fmt_odds(winner_odds)} vs {_fmt_odds(loser_odds)})"


def _summary_key(day: dt.date, group: str, tournament: str, status: str, stage: str) -> str:
    return "|".join([day.isoformat(), group or "", status or "", tournament or "", stage or ""])


def _header(tournament: str, group: str, stage: str) -> str:
    emoji = "🙋🏼‍♀️" if group == "women" else "🙋🏼‍♂️"
    gender = "женщины" if group == "women" else "мужчины"
    return f"{emoji} {tournament}, {gender}, {stage.lower() if stage else 'игровой день'}"


def _build_summary_text(day: dt.date, group: str, tournament: str, stage: str, events: List[Dict[str, Any]], odds_map: Dict[int, Dict[str, Any]]) -> str:
    buckets: Dict[str, List[str]] = defaultdict(list)
    for event in sorted(events, key=lambda x: (x.get("start_ts") or 0, x.get("home_name") or "")):
        line = _result_line(event)
        if not line:
            continue
        odds = odds_map.get(int(event["event_id"]))
        category = _category_for(event, odds)
        if category == "unexpected":
            line = _line_with_average_odds(event, line, odds)
        buckets[category].append(line)
    if not any(buckets.values()):
        return ""

    sections = [
        ("unexpected", "⚡ Сенсации"),
        ("expected", "👌🏻 Ожидаемо"),
        ("pickem", "🟰Когда шансы 50/50"),
        ("sad", "😥  Грустно"),
        ("no_odds", "Без коэффициентов"),
    ]
    lines = ["📊 Результаты игрового дня", "", _header(tournament, group, stage)]
    for key, title in sections:
        if not buckets.get(key):
            continue
        lines.extend(["", title, "", *buckets[key]])
    return "\n".join(lines).strip()


def _target_groups(events: List[Dict[str, Any]]) -> Iterable[tuple[str, str, str, List[Dict[str, Any]]]]:
    grouped: Dict[tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        if _is_target_event(event):
            grouped[(str(event.get("tour_group") or ""), str(event.get("tournament_name") or ""), str(event.get("tournament_status") or ""))].append(event)
    for (group, tournament, status), rows in grouped.items():
        yield group, tournament, status, rows


def _target_sort_key(group: str, tournament: str, status: str, rows: List[Dict[str, Any]]) -> tuple[int, str, str, str]:
    ranks: List[int] = []
    for event in rows:
        try:
            ranks.append(int(event.get("tournament_sort_rank", 9)))
        except Exception:
            pass
    rank = min(ranks) if ranks else 9
    return rank, group or "", status or "", tournament or ""


def summary_tournaments_for_menu(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    odds_map = get_match_odds_map([int(event["event_id"]) for event in events if event.get("event_id")])
    items: List[Dict[str, Any]] = []
    for group, tournament, status, rows in _target_groups(events):
        finished = sum(1 for event in rows if ss.is_finished(event))
        live = sum(1 for event in rows if str(event.get("status_type") or "").lower() in {"inprogress", "live"})
        items.append(
            {
                "tour_group": group,
                "tournament_name": tournament,
                "tournament_status": status,
                "matches_count": len(rows),
                "finished_count": finished,
                "live_count": live,
                "has_odds": any(odds_map.get(int(event["event_id"])) for event in rows if event.get("event_id")),
                "all_finished": bool(rows) and finished == len(rows),
                "sort_key": _target_sort_key(group, tournament, status, rows),
            }
        )
    items.sort(key=lambda item: item["sort_key"])
    return items


def build_daily_summary_for_tournament(
    day: dt.date,
    events: List[Dict[str, Any]],
    group: str,
    tournament: str,
    status: str = "",
) -> tuple[str, str, str]:
    rows = [
        event
        for event in events
        if _is_target_event(event)
        and str(event.get("tour_group") or "") == str(group or "")
        and str(event.get("tournament_name") or "") == str(tournament or "")
        and (not status or str(event.get("tournament_status") or "") == str(status or ""))
    ]
    if not rows:
        return "", "", ""
    stage = _common_stage(rows)
    odds_map = get_match_odds_map([int(event["event_id"]) for event in rows if event.get("event_id")])
    return _build_summary_text(day, group, tournament, stage, rows, odds_map), status or "", stage


def mark_daily_summary_for_tournament(day: dt.date, group: str, tournament: str, status: str, stage: str) -> None:
    mark_daily_summary_sent(_summary_key(day, group, tournament, status, stage), day, group, tournament, status, stage)


def publish_daily_summaries(day: dt.date, events: List[Dict[str, Any]], bot_token: str, chat_id: int | str) -> int:
    if not enabled() or not bot_token or not chat_id:
        return 0
    sent = 0
    odds_map = get_match_odds_map([int(event["event_id"]) for event in events if event.get("event_id")])
    for group, tournament, status, rows in _target_groups(events):
        if not rows or not all(ss.is_finished(event) for event in rows):
            continue
        if _requires_odds() and not any(odds_map.get(int(event["event_id"])) for event in rows):
            continue
        stage = _common_stage(rows)
        key = _summary_key(day, group, tournament, status, stage)
        if is_daily_summary_sent(key):
            continue
        text = _build_summary_text(day, group, tournament, stage, rows, odds_map)
        if not text:
            continue
        if _send_message(bot_token, _summary_chat_id(chat_id), text):
            mark_daily_summary_sent(key, day, group, tournament, status, stage)
            sent += 1
    return sent


def _send_message(bot_token: str, chat_id: int | str, text: str) -> bool:
    payload = json.dumps({"chat_id": chat_id, "text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as exc:
        print(f"[summary] send failed: {exc}")
        return False
