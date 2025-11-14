# db_pg.py
from __future__ import annotations
import os, json
from contextlib import contextmanager
from datetime import date
from typing import Iterable, Tuple, List, Optional
import psycopg

POSTGRES_URL = os.getenv("POSTGRES_URL", "").strip()
if not POSTGRES_URL:
    raise RuntimeError("POSTGRES_URL is not set")

@contextmanager
def _conn():
    with psycopg.connect(POSTGRES_URL, autocommit=True) as con:
        yield con

def ensure_schema() -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id BIGINT PRIMARY KEY,
            tz TEXT NOT NULL DEFAULT 'Europe/Helsinki'
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS watch_today (
            chat_id BIGINT NOT NULL,
            label TEXT NOT NULL,
            src   TEXT NOT NULL DEFAULT 'sofascore',
            ds    DATE NOT NULL,
            PRIMARY KEY (chat_id, label, ds)
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events_cache (
            ds   DATE PRIMARY KEY,
            data JSONB NOT NULL,
            ts   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """)

def ensure_user(chat_id: int) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("INSERT INTO users(chat_id) VALUES (%s) ON CONFLICT DO NOTHING;", (chat_id,))

def set_tz(chat_id: int, tz: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("UPDATE users SET tz=%s WHERE chat_id=%s;", (tz, chat_id))

def get_tz(chat_id: int) -> str:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT tz FROM users WHERE chat_id=%s;", (chat_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else "Europe/Helsinki"

def add_watch(chat_id: int, label: str, src: str, ds: date) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO watch_today(chat_id, label, src, ds)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id, label, ds) DO NOTHING;
        """, (chat_id, label, src, ds))

def clear_today(chat_id: int, ds: date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM watch_today WHERE chat_id=%s AND ds=%s;", (chat_id, ds))
        return cur.rowcount

def list_today(chat_id: int, ds: date) -> List[Tuple[str, Optional[str], str]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        SELECT label, NULL as resolved, src
        FROM watch_today
        WHERE chat_id=%s AND ds=%s
        ORDER BY label;
        """, (chat_id, ds))
        return cur.fetchall()

def set_events_cache(ds: date, events: List[dict]) -> None:
    payload = json.dumps(events, ensure_ascii=False)
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO events_cache(ds, data)
        VALUES (%s, %s::jsonb)
        ON CONFLICT (ds) DO UPDATE
        SET data=EXCLUDED.data, ts=now();
        """, (ds, payload))

def get_events_cache(ds: date) -> List[dict]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT data FROM events_cache WHERE ds=%s;", (ds,))
        row = cur.fetchone()
        if not row:
            return []
        val = row[0]
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return []
        return val or []
