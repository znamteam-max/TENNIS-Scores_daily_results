from __future__ import annotations

import re
from typing import Any, Dict, Optional

from providers import sofascore as ss


STATUS_ICONS = {
    "notstarted": "⏳",
    "inprogress": "🟢",
    "live": "🟢",
    "finished": "✅",
    "retired": "⚠️",
    "interrupted": "⚠️",
    "walkover": "⚠️",
    "cancelled": "🚫",
}


def _number(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def _score_obj(match: Dict[str, Any], side: str) -> Dict[str, Any]:
    raw = match.get("raw") or {}
    value = raw.get("homeScore" if side == "home" else "awayScore") or {}
    return value if isinstance(value, dict) else {}


def _set_total(score: Dict[str, Any]) -> Optional[int]:
    for key in ("current", "display", "normaltime"):
        value = _number(score.get(key))
        if value is not None and 0 <= value <= 5:
            return value
    return None


def _period_pairs(match: Dict[str, Any]) -> list[tuple[int, int]]:
    home = _score_obj(match, "home")
    away = _score_obj(match, "away")
    out: list[tuple[int, int]] = []
    for idx in range(1, 6):
        h = _number(home.get(f"period{idx}"))
        a = _number(away.get(f"period{idx}"))
        if h is None or a is None:
            continue
        out.append((h, a))
    return out


def _set_finished(home: int, away: int) -> bool:
    high, low = max(home, away), min(home, away)
    if high >= 7 and high > low:
        return True
    return high >= 6 and high - low >= 2


def _sets_from_periods(pairs: list[tuple[int, int]], *, include_last: bool = True) -> tuple[int, int]:
    home_sets = 0
    away_sets = 0
    rows = pairs if include_last else pairs[:-1]
    for home, away in rows:
        if not _set_finished(home, away):
            continue
        if home > away:
            home_sets += 1
        elif away > home:
            away_sets += 1
    return home_sets, away_sets


def compact_match_score(match: Dict[str, Any]) -> str:
    status = ss.status_type(match)
    if status in {"notstarted", "cancelled"}:
        return ""

    home_score = _score_obj(match, "home")
    away_score = _score_obj(match, "away")
    home_sets = _set_total(home_score)
    away_sets = _set_total(away_score)
    pairs = _period_pairs(match)

    if home_sets is None or away_sets is None:
        inferred = _sets_from_periods(pairs)
        home_sets = inferred[0] if home_sets is None else home_sets
        away_sets = inferred[1] if away_sets is None else away_sets

    if status in {"finished", "retired", "walkover"}:
        if home_sets is not None and away_sets is not None:
            return f"{home_sets}:{away_sets}"
        return ""

    if status not in {"inprogress", "live", "interrupted"}:
        return ""

    sets_text = ""
    if home_sets is not None and away_sets is not None and (home_sets or away_sets):
        sets_text = f"{home_sets}:{away_sets}"

    current = pairs[-1] if pairs else None
    if current and not _set_finished(*current):
        games = f"({current[0]}:{current[1]})"
        return f"{sets_text} {games}".strip()

    # Between sets Flashscore can still report the just-finished set as the last period.
    # In that case the set score alone is the useful compact state.
    if sets_text:
        return sets_text

    if current:
        return f"({current[0]}:{current[1]})"
    return ""


def _initial(value: str) -> str:
    value = str(value or "").strip(" .,-")
    return f"{value[0].upper()}." if value else ""


def _is_initial(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё]\.?", token.strip()))


def _short_surname(surname: str, limit: int = 13) -> str:
    surname = " ".join(str(surname or "").split()).strip(" ,")
    if not surname:
        return ""

    # Long double surnames are the main source of truncated Telegram buttons.
    if "-" in surname:
        parts = [part for part in surname.split("-") if part]
        if len(parts) >= 2 and len(surname) > limit:
            compact = f"{parts[0]}-{_initial(parts[1])}"
            if len(compact) <= limit + 2:
                return compact
            return parts[0]

    # Keep common surname particles together when they are still compact.
    words = surname.split()
    if len(words) > 1:
        particles = {"de", "del", "da", "di", "du", "van", "von", "der", "la", "le", "де", "ван", "фон"}
        if words[0].lower() in particles:
            compact = f"{words[0]} {words[-1]}"
            if len(compact) <= limit:
                return compact
        surname = words[-1]

    if len(surname) <= limit:
        return surname
    return surname[: max(5, limit - 1)].rstrip("-") + "…"


def compact_player_name(value: Any) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split()).strip()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if not text:
        return "TBD"

    # Doubles are rare in this UI, but keep them readable if they appear.
    if "/" in text:
        sides = [compact_player_name(part) for part in text.split("/") if part.strip()]
        return "/".join(sides)

    if "," in text:
        surname, given = [part.strip() for part in text.split(",", 1)]
        return " ".join(part for part in (_short_surname(surname), _initial(given)) if part)

    tokens = text.split()
    if len(tokens) == 1:
        return _short_surname(tokens[0])

    if _is_initial(tokens[-1]):
        surname = " ".join(tokens[:-1])
        return f"{_short_surname(surname)} {_initial(tokens[-1])}".strip()

    if _is_initial(tokens[0]):
        surname = " ".join(tokens[1:])
        return f"{_short_surname(surname)} {_initial(tokens[0])}".strip()

    # Russian aliases in the bot are normally "Имя Фамилия".
    given = tokens[0]
    surname_words = tokens[1:]
    surname = " ".join(surname_words)
    return f"{_short_surname(surname)} {_initial(given)}".strip()


def install(module: Any) -> None:
    def match_label(chat_id: int, match: Dict[str, Any], selected: bool) -> str:
        status = ss.status_type(match)
        icon = STATUS_ICONS.get(status, "•")
        time_text = module._fmt_ts(chat_id, match.get("start_ts"))
        score = compact_match_score(match)

        home_raw = match.get("home_name") or "TBD"
        away_raw = match.get("away_name") or "TBD"
        try:
            home_raw = module._display_side_name(home_raw)
            away_raw = module._display_side_name(away_raw)
        except Exception:
            pass

        home = compact_player_name(home_raw)
        away = compact_player_name(away_raw)
        prefix = ["[x]" if selected else "[ ]", icon]
        if time_text:
            prefix.append(time_text)
        if score:
            prefix.append(score)
        label = " ".join(prefix) + f" {home} — {away}"
        return module._cut(label, 62)

    module._match_label = match_label
