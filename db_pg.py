
from __future__ import annotations
import os
from typing import Iterable, Tuple, Optional, List
from datetime import date
import psycopg

POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("POSTGRES_DATABASE_URL") or os.getenv("POSTGRES_PRISMA_URL")

DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        chat_id BIGINT PRIMARY KEY,
        tz TEXT DEFAULT 'Europe/Helsinki',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );""",
    """CREATE TABLE IF NOT EXISTS watchlist (
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        label TEXT NOT NULL,
        resolved_name TEXT,
        provider TEXT NOT NULL,
        provider_player_id TEXT,
        expires_on DATE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );""",
    """CREATE UNIQUE INDEX IF NOT EXISTS u_watchlist_daily 
        ON watchlist(chat_id, label, provider, expires_on);""",
    """CREATE TABLE IF NOT EXISTS notified (
        id BIGSERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        provider TEXT NOT NULL,
        event_id TEXT NOT NULL,
        event_day DATE NOT NULL,
        sent_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(chat_id, provider, event_id, event_day)
    );"""
]

def _conn():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not set")
    return psycopg.connect(POSTGRES_URL, autocommit=True)

def ensure_schema():
    with _conn() as con:
        with con.cursor() as cur:
            for ddl in DDL:
                cur.execute(ddl)

def ensure_user(chat_id: int, tz: str = "Europe/Helsinki"):
    with _conn() as con, con.cursor() as cur:
        cur.execute("INSERT INTO users(chat_id, tz) VALUES (%s, %s) ON CONFLICT (chat_id) DO NOTHING", (chat_id, tz))

def set_tz(chat_id: int, tz: str):
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE users SET tz=%s WHERE chat_id=%s", (tz, chat_id))

def get_tz(chat_id: int) -> str:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT tz FROM users WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else "Europe/Helsinki"

def all_chat_ids() -> List[int]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT chat_id FROM users")
        return [r[0] for r in cur.fetchall()]

def add_watch(chat_id: int, label: str, provider: str, expires_on: date, resolved_name: str = None, provider_player_id: str = None):
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO watchlist(chat_id, label, provider, expires_on, resolved_name, provider_player_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (chat_id, label, provider, expires_on, resolved_name, provider_player_id)
        )

def remove_watch(chat_id: int, label: str, expires_on: date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM watchlist WHERE chat_id=%s AND label=%s AND expires_on=%s", (chat_id, label, expires_on))
        return cur.rowcount

def clear_today(chat_id: int, expires_on: date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM watchlist WHERE chat_id=%s AND expires_on=%s", (chat_id, expires_on))
        return cur.rowcount

def list_today(chat_id: int, expires_on: date):
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT label, resolved_name, provider_player_id FROM watchlist WHERE chat_id=%s AND expires_on=%s ORDER BY label ASC", (chat_id, expires_on))
        return cur.fetchall()

def mark_notified(chat_id: int, provider: str, event_id: str, event_day: date):
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO notified(chat_id, provider, event_id, event_day) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (chat_id, provider, event_id, event_day)
        )

def was_notified(chat_id: int, provider: str, event_id: str, event_day: date) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT 1 FROM notified WHERE chat_id=%s AND provider=%s AND event_id=%s AND event_day=%s", (chat_id, provider, event_id, event_day))
        return cur.fetchone() is not None
