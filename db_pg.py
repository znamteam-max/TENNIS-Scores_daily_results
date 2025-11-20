# db_pg.py
from __future__ import annotations
import os, json, datetime as dt
from typing import Optional, List, Tuple, Dict, Any
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
        tz text not null default 'Europe/London'
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
        -- колонку data добавим ниже через ALTER IF NOT EXISTS
    );
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql)
        # гарантируем колонку data jsonb
        cur.execute("""
            do $$
            begin
                if not exists (
                    select 1 from information_schema.columns
                    where table_name='events_cache' and column_name='data'
                ) then
                    alter table events_cache add column data jsonb not null default '{}'::jsonb;
                end if;
                if not exists (
                    select 1 from information_schema.columns
                    where table_name='events_cache' and column_name='updated_at'
                ) then
                    alter table events_cache add column updated_at timestamptz not null default now();
                end if;
            end$$;
        """)

def ping_db() -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select 1")
        return True

# ---------- TZ ----------
def get_tz(chat_id: int) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from chats where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else None

def set_tz(chat_id: int, tz: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into chats (chat_id, tz)
            values (%s, %s)
            on conflict (chat_id) do update set tz=excluded.tz
        """, (chat_id, tz))

# ---------- aliases ----------
def norm_key(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def set_alias(en_full: str, ru: str) -> None:
    en_full = en_full.strip()
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into name_aliases (en_full, ru)
            values (%s, %s)
            on conflict (en_full) do update set ru=excluded.ru
        """, (en_full, ru.strip()))

def ru_name_for(en_full: str) -> Optional[Tuple[str, bool]]:
    en_full = en_full.strip()
    with _conn() as con, con.cursor() as cur:
        cur.execute("select ru from name_aliases where en_full=%s", (en_full,))
        row = cur.fetchone()
        if row:
            return (row[0], True)
        return ("", False)

def set_pending_alias(chat_id: int, en_full: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into pending_alias (chat_id, en_full)
            values (%s, %s)
            on conflict (chat_id) do update set en_full=excluded.en_full
        """, (chat_id, en_full.strip()))

def consume_pending_alias(chat_id: int) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select en_full from pending_alias where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return None
        en_full = row[0]
        cur.execute("delete from pending_alias where chat_id=%s", (chat_id,))
        return en_full

# ---------- watches ----------
def add_watch(chat_id: int, name_en: str, day: dt.date) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into watches (chat_id, day, name_en)
            values (%s, %s, %s)
            on conflict do nothing
        """, (chat_id, day, name_en.strip()))

def add_watches(chat_id: int, day: dt.date, names_en: List[str]) -> int:
    cnt = 0
    with _conn() as con, con.cursor() as cur:
        for raw in names_en:
            name_en = (raw or "").strip()
            if not name_en:
                continue
            cur.execute("""
                insert into watches (chat_id, day, name_en)
                values (%s, %s, %s)
                on conflict do nothing
            """, (chat_id, day, name_en))
            cnt += 1
    return cnt

def remove_watch(chat_id: int, day: dt.date, name_en: str) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and day=%s and name_en=%s",
                    (chat_id, day, name_en.strip()))
        return cur.rowcount > 0

def list_today(chat_id: int, day: dt.date) -> List[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            select name_en from watches where chat_id=%s and day=%s order by name_en
        """, (chat_id, day))
        return [r[0] for r in cur.fetchall()]

# ---------- events cache ----------
def set_events_cache(ds: dt.date, data: Dict[str, Any]) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into events_cache (ds, data, updated_at)
            values (%s, %s::jsonb, now())
            on conflict (ds) do update set data=excluded.data, updated_at=now()
        """, (ds, json.dumps(data)))

def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select data from events_cache where ds=%s", (ds,))
        row = cur.fetchone()
        return row[0] if row else None
