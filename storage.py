import sqlite3
from contextlib import contextmanager
from typing import List, Tuple, Optional, Dict
from datetime import datetime, date

class Storage:
    def __init__(self, path: str):
        self.path = path
        self._init_db()

    def _init_db(self):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                tz TEXT DEFAULT 'Europe/Helsinki',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                label TEXT NOT NULL,              -- what the user typed
                resolved_name TEXT,               -- normalized/official name (if resolved)
                provider TEXT NOT NULL,           -- 'sofascore'
                provider_player_id TEXT,          -- optional: provider player/team id (string to be flexible)
                expires_on DATE,                  -- validity date (YYYY-MM-DD), resets daily
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS u_watchlist_daily 
            ON watchlist(chat_id, label, provider, expires_on);
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS notified (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_day DATE NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, provider, event_id, event_day)
            );
            """)
            con.commit()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path)
        try:
            yield con
        finally:
            con.close()

    def ensure_user(self, chat_id: int, tz: str = "Europe/Helsinki"):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO users(chat_id, tz) VALUES (?, ?)", (chat_id, tz))
            con.commit()

    def set_tz(self, chat_id: int, tz: str):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("UPDATE users SET tz=? WHERE chat_id=?", (tz, chat_id))
            con.commit()

    def get_tz(self, chat_id: int) -> str:
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("SELECT tz FROM users WHERE chat_id=?", (chat_id,))
            row = cur.fetchone()
            return row[0] if row else 'Europe/Helsinki'

    def add_watch(self, chat_id: int, label: str, provider: str, expires_on: str, 
                  resolved_name: str = None, provider_player_id: str = None):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute(
                """INSERT OR IGNORE INTO watchlist(chat_id, label, provider, expires_on, resolved_name, provider_player_id) 
                    VALUES (?, ?, ?, ?, ?, ?)""", 
                (chat_id, label, provider, expires_on, resolved_name, provider_player_id))
            con.commit()

    def remove_watch(self, chat_id: int, label: str, expires_on: str) -> int:
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("DELETE FROM watchlist WHERE chat_id=? AND label=? AND expires_on=?", (chat_id, label, expires_on))
            con.commit()
            return cur.rowcount

    def clear_today(self, chat_id: int, expires_on: str) -> int:
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("DELETE FROM watchlist WHERE chat_id=? AND expires_on=?", (chat_id, expires_on))
            con.commit()
            return cur.rowcount

    def list_today(self, chat_id: int, expires_on: str):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("SELECT label, resolved_name, provider_player_id FROM watchlist WHERE chat_id=? AND expires_on=? ORDER BY label ASC", (chat_id, expires_on))
            return cur.fetchall()

    def mark_notified(self, chat_id: int, provider: str, event_id: str, event_day: str):
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("INSERT OR IGNORE INTO notified(chat_id, provider, event_id, event_day) VALUES (?, ?, ?, ?)", 
                        (chat_id, provider, event_id, event_day))
            con.commit()

    def was_notified(self, chat_id: int, provider: str, event_id: str, event_day: str) -> bool:
        with self._conn() as con:
            cur = con.cursor()
            cur.execute("SELECT 1 FROM notified WHERE chat_id=? AND provider=? AND event_id=? AND event_day=?", 
                        (chat_id, provider, event_id, event_day))
            return cur.fetchone() is not None
