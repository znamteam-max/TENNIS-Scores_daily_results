from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, List, Tuple

import db_pg
import player_alias_admin_patch as admin

_INSTALLED = False

_CYR = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y",
    "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"kh","ц":"tz","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _latinize(value: Any) -> str:
    text = _norm(value).lower()
    text = text.replace("đ", "d").replace("ð", "d").replace("ł", "l").replace("ø", "o")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i:i+2] == "дж":
            out.append("dj")
            i += 2
            continue
        out.append(_CYR.get(text[i], text[i]))
        i += 1
    text = "".join(out)
    # Search-oriented phonetic simplification: Джокович -> djokovic.
    text = text.replace("dzh", "dj").replace("shch", "sh").replace("ch", "c")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _score(query: str, source: str, alias: str = "") -> float:
    q = _latinize(query)
    if not q:
        return 0.0
    best = 0.0
    for value in (source, alias):
        key = _latinize(value)
        if not key:
            continue
        if key == q:
            best = max(best, 100.0)
        elif key.startswith(q) or q.startswith(key):
            best = max(best, 92.0 - abs(len(key) - len(q)) * 0.2)
        elif q in key:
            best = max(best, 86.0 - abs(len(key) - len(q)) * 0.1)
        else:
            ratio = difflib.SequenceMatcher(None, q, key).ratio()
            if ratio >= 0.72:
                best = max(best, ratio * 80.0)
    return best


def _all_aliases(limit: int = 5000) -> List[Tuple[str, str]]:
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("select en_full, ru from name_aliases order by en_full limit %s", (int(limit),))
        return [(str(a), str(b)) for a, b in cur.fetchall()]


def _recent_source_names(limit: int = 7000) -> List[str]:
    names: List[str] = []
    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                with recent as (
                    select data
                      from events_cache
                     where data is not null
                     order by ds desc
                     limit 21
                ), events as (
                    select jsonb_array_elements(coalesce(data->'events', '[]'::jsonb)) ev
                      from recent
                )
                select distinct name
                  from (
                    select coalesce(ev->'homeCompetitor'->>'name', ev->'homePlayer'->>'name', ev->'homeTeam'->>'name', ev->'home'->>'name') name from events
                    union all
                    select coalesce(ev->'awayCompetitor'->>'name', ev->'awayPlayer'->>'name', ev->'awayTeam'->>'name', ev->'away'->>'name') name from events
                    union all
                    select coalesce(ev->'homeCompetitor'->>'shortName', ev->'homePlayer'->>'shortName', ev->'homeTeam'->>'shortName') name from events
                    union all
                    select coalesce(ev->'awayCompetitor'->>'shortName', ev->'awayPlayer'->>'shortName', ev->'awayTeam'->>'shortName') name from events
                  ) x
                 where name is not null and btrim(name) <> ''
                 limit %s
                """,
                (int(limit),),
            )
            names.extend(str(row[0]) for row in cur.fetchall() if row and row[0])
    except Exception as exc:
        print(f"[players-v2] event names search failed: {exc}")

    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                select distinct name from (
                    select home_name name from match_watches
                    union all
                    select away_name name from match_watches
                ) x
                where name is not null and btrim(name) <> ''
                limit 3000
                """
            )
            names.extend(str(row[0]) for row in cur.fetchall() if row and row[0])
    except Exception as exc:
        print(f"[players-v2] watch names search failed: {exc}")
    return names


def search_players(query: str, limit: int = 30) -> List[Tuple[str, str]]:
    query = _norm(query)
    if not query:
        return []

    aliases = _all_aliases()
    alias_exact: Dict[str, str] = {src: ru for src, ru in aliases}
    candidates: Dict[str, str] = {}

    # Saved aliases are first-class players: they must remain searchable even when
    # the player is no longer present in recent schedules.
    for source, alias in aliases:
        candidates[source] = alias

    for source in _recent_source_names():
        source = _norm(source)
        if source and source.upper() != "TBD":
            candidates.setdefault(source, alias_exact.get(source, ""))

    scored: List[Tuple[float, str, str]] = []
    for source, alias in candidates.items():
        rank = _score(query, source, alias)
        if rank > 0:
            scored.append((rank, source, alias))

    scored.sort(key=lambda row: (-row[0], len(row[1]), row[1].lower()))
    return [(source, alias) for _rank, source, alias in scored[:limit]]


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # The existing admin UI resolves this global at search time, so replacing it
    # upgrades the UI without duplicating all callback code.
    admin._search_known_players = search_players

    old_text = module._handle_text

    def text_handler(chat_id, text, user_id=None):
        raw = _norm(text)
        cmd = raw.split(" ", 1)[0].lower() if raw else ""
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        # Commands still belong to the existing handler.
        if cmd.startswith("/") or cmd in {"players", "cancel", "отмена"}:
            return old_text(chat_id, text, user_id=user_id)

        try:
            state, _payload = module.get_state(chat_id)
        except Exception:
            state = ""

        # While anywhere inside the player directory, typing a new name always
        # means a new search. No need to press "Новый поиск" first.
        if state in {"players_results", "players_record"} and raw:
            module.set_state(chat_id, "players_search", {})
            return old_text(chat_id, raw, user_id=user_id)

        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
    _INSTALLED = True
