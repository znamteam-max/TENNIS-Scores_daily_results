from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import psycopg

POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")


def _conn():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(POSTGRES_URL, autocommit=True)


def ensure_schema() -> None:
    sql = """
    create table if not exists chats (
        chat_id bigint primary key,
        tz text not null default 'Europe/Tallinn'
    );

    create table if not exists name_aliases (
        en_full text primary key,
        ru text not null
    );

    create table if not exists pending_alias (
        chat_id bigint primary key,
        en_full text not null
    );

    create table if not exists watches (
        chat_id bigint not null,
        day date not null,
        name_en text not null,
        primary key (chat_id, day, name_en)
    );

    create table if not exists events_cache (
        ds date primary key
    );

    alter table events_cache add column if not exists data jsonb;
    alter table events_cache add column if not exists updated_at timestamptz not null default now();

    create table if not exists user_states (
        chat_id bigint primary key,
        state text not null,
        payload jsonb not null default '{}'::jsonb,
        updated_at timestamptz not null default now()
    );

    create table if not exists match_watches (
        chat_id bigint not null,
        day date not null,
        event_id bigint not null,
        category text not null,
        tournament_name text not null,
        home_name text not null,
        away_name text not null,
        start_ts bigint,
        primary key (chat_id, day, event_id)
    );
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql)


def ping_db() -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select 1")
        return True


def get_tz(chat_id: int) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from chats where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_tz(chat_id: int, tz: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into chats (chat_id, tz)
            values (%s, %s)
            on conflict (chat_id) do update set tz=excluded.tz
            """,
            (chat_id, tz),
        )


def set_alias(en_full: str, ru: str) -> None:
    en_full = (en_full or "").strip()
    ru = (ru or "").strip()
    if not en_full or not ru:
        return
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into name_aliases (en_full, ru)
            values (%s, %s)
            on conflict (en_full) do update set ru=excluded.ru
            """,
            (en_full, ru),
        )


def ru_name_for(en_full: str) -> Tuple[str, bool]:
    en_full = (en_full or "").strip()
    if not en_full:
        return ("", False)
    with _conn() as con, con.cursor() as cur:
        cur.execute("select ru from name_aliases where en_full=%s", (en_full,))
        row = cur.fetchone()
        if row and row[0]:
            return (row[0], True)
        return ("", False)


def set_pending_alias(chat_id: int, en_full: str) -> None:
    en_full = (en_full or "").strip()
    if not en_full:
        return
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into pending_alias (chat_id, en_full)
            values (%s, %s)
            on conflict (chat_id) do update set en_full=excluded.en_full
            """,
            (chat_id, en_full),
        )


def consume_pending_alias(chat_id: int) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select en_full from pending_alias where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return None
        en_full = row[0]
        cur.execute("delete from pending_alias where chat_id=%s", (chat_id,))
        return en_full


def add_watch(chat_id: int, name_en: str, day: dt.date) -> None:
    name_en = (name_en or "").strip()
    if not name_en:
        return
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into watches (chat_id, day, name_en)
            values (%s, %s, %s)
            on conflict do nothing
            """,
            (chat_id, day, name_en),
        )


def add_watches(chat_id: int, day: dt.date, names_en: List[str]) -> int:
    cnt = 0
    with _conn() as con, con.cursor() as cur:
        for raw in names_en:
            name_en = (raw or "").strip()
            if not name_en:
                continue
            cur.execute(
                """
                insert into watches (chat_id, day, name_en)
                values (%s, %s, %s)
                on conflict do nothing
                """,
                (chat_id, day, name_en),
            )
            cnt += 1
    return cnt


def remove_watch(chat_id: int, day: dt.date, name_en: str) -> bool:
    name_en = (name_en or "").strip()
    if not name_en:
        return False
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "delete from watches where chat_id=%s and day=%s and name_en=%s",
            (chat_id, day, name_en),
        )
        return cur.rowcount > 0


def list_today(chat_id: int, day: dt.date) -> List[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select name_en
            from watches
            where chat_id=%s and day=%s
            order by name_en
            """,
            (chat_id, day),
        )
        return [r[0] for r in cur.fetchall()]


def set_events_cache(ds: dt.date, data: Dict[str, Any]) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into events_cache (ds, data, updated_at)
            values (%s, %s::jsonb, now())
            on conflict (ds) do update
            set data=excluded.data, updated_at=now()
            """,
            (ds, json.dumps(data)),
        )


def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select data from events_cache where ds=%s", (ds,))
        row = cur.fetchone()
        return row[0] if row else None


def set_state(chat_id: int, state: str, payload: Optional[Dict[str, Any]] = None) -> None:
    payload = payload or {}
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into user_states (chat_id, state, payload, updated_at)
            values (%s, %s, %s::jsonb, now())
            on conflict (chat_id) do update
            set state=excluded.state, payload=excluded.payload, updated_at=now()
            """,
            (chat_id, state, json.dumps(payload)),
        )


def get_state(chat_id: int) -> Tuple[Optional[str], Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select state, payload from user_states where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return (None, {})
        return (row[0], row[1] or {})


def clear_state(chat_id: int) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from user_states where chat_id=%s", (chat_id,))


def add_match_watch(chat_id: int, day: dt.date, match_row: Dict[str, Any]) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into match_watches (
                chat_id, day, event_id, category, tournament_name,
                home_name, away_name, start_ts
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            """,
            (
                chat_id,
                day,
                int(match_row["event_id"]),
                str(match_row["category"]),
                str(match_row["tournament_name"]),
                str(match_row["home_name"]),
                str(match_row["away_name"]),
                match_row.get("start_ts"),
            ),
        )
        return cur.rowcount > 0


def remove_match_watch(chat_id: int, day: dt.date, event_id: int) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            delete from match_watches
            where chat_id=%s and day=%s and event_id=%s
            """,
            (chat_id, day, int(event_id)),
        )
        return cur.rowcount > 0


def list_match_watches(chat_id: int, day: dt.date) -> List[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select event_id, category, tournament_name, home_name, away_name, start_ts
            from match_watches
            where chat_id=%s and day=%s
            order by tournament_name, start_ts nulls last, home_name, away_name
            """,
            (chat_id, day),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "event_id": r[0],
                    "category": r[1],
                    "tournament_name": r[2],
                    "home_name": r[3],
                    "away_name": r[4],
                    "start_ts": r[5],
                }
            )
        return rows
