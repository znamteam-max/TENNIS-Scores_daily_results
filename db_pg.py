from __future__ import annotations

import os
import datetime as dt
from typing import Optional, Iterable, Tuple, List

import psycopg


# ---------- connection ----------

def _pg_url() -> str:
    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set")
    return url


def _conn() -> psycopg.Connection:
    # psycopg v3
    return psycopg.connect(_pg_url())


# ---------- schema ----------

def ensure_schema() -> None:
    with _conn() as con, con.cursor() as cur:
        # prefs (таймзона и др)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_prefs (
            chat_id BIGINT PRIMARY KEY,
            tz TEXT,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        # алиасы имен
        cur.execute("""
        CREATE TABLE IF NOT EXISTS player_names (
            en_key TEXT PRIMARY KEY,
            en_full TEXT NOT NULL,
            ru_name TEXT,
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        # список наблюдаемых игроков на дату
        cur.execute("""
        CREATE TABLE IF NOT EXISTS watches (
            chat_id BIGINT NOT NULL,
            day DATE NOT NULL,
            name_en TEXT NOT NULL,
            PRIMARY KEY (chat_id, day, name_en)
        );
        """)

        # кэш расписаний
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events_cache (
            ds DATE PRIMARY KEY,
            json JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        # ожидание русского алиаса (между вопросом и ответом)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_alias (
            chat_id BIGINT PRIMARY KEY,
            en_full TEXT NOT NULL,
            asked_at TIMESTAMPTZ DEFAULT now()
        );
        """)

        con.commit()


# ---------- prefs (timezone) ----------

def get_tz(chat_id: int) -> str:
    default_tz = os.getenv("APP_TZ", "Europe/Helsinki")
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT tz FROM chat_prefs WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else default_tz


def set_tz(chat_id: int, tz: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO chat_prefs (chat_id, tz, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (chat_id) DO UPDATE
           SET tz = EXCLUDED.tz, updated_at = now();
        """, (chat_id, tz))
        con.commit()


# ---------- name helpers / aliases ----------

def norm_key(s: str) -> str:
    return "".join(ch.lower() for ch in s.strip() if ch.isalnum() or ch == " ")


def _is_cyrillic(s: str) -> bool:
    return any(("А" <= ch <= "я") or (ch in ("ё", "Ё")) for ch in s)


def set_alias(en_full: str, ru_name: str) -> None:
    en_key = norm_key(en_full)
    ru = ru_name.strip().replace("Ё", "Е").replace("ё", "е")
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO player_names (en_key, en_full, ru_name, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (en_key) DO UPDATE
           SET en_full = EXCLUDED.en_full,
               ru_name = EXCLUDED.ru_name,
               updated_at = now();
        """, (en_key, en_full.strip(), ru))
        con.commit()


def get_player_ru(name_or_en: str) -> Optional[str]:
    s = (name_or_en or "").strip()
    if not s:
        return None
    if _is_cyrillic(s):
        return s.replace("Ё", "Е").replace("ё", "е")
    en_key = norm_key(s)
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT ru_name FROM player_names WHERE en_key=%s", (en_key,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def ru_name_for(name: str) -> Tuple[Optional[str], bool]:
    """
    Возвращает (ru_or_none, known_bool).
    known=True, если:
      - пользователь уже ввёл кириллицу, или
      - для EN есть сохранённый RU-алиас.
    """
    if not name:
        return None, False
    s = name.strip()
    if _is_cyrillic(s):
        return s.replace("Ё", "Е").replace("ё", "е"), True
    ru = get_player_ru(s)
    return (ru, True) if ru else (None, False)


# ---------- pending alias (dialog state) ----------

def set_pending_alias(chat_id: int, en_full: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO pending_alias (chat_id, en_full, asked_at)
        VALUES (%s, %s, now())
        ON CONFLICT (chat_id) DO UPDATE
           SET en_full = EXCLUDED.en_full, asked_at = now();
        """, (chat_id, en_full.strip()))
        con.commit()


def consume_pending_alias(chat_id: int) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT en_full FROM pending_alias WHERE chat_id=%s", (chat_id,))
        row = cur.fetchone()
        cur.execute("DELETE FROM pending_alias WHERE chat_id=%s", (chat_id,))
        con.commit()
        return row[0] if row else None


# ---------- watches (followed players per day) ----------

def add_watches(chat_id: int, day: dt.date, names: Iterable[str]) -> int:
    """
    Принимает список строк (EN/RU). В таблицу кладём, как пришло.
    """
    cnt = 0
    with _conn() as con, con.cursor() as cur:
        for raw in names:
            if isinstance(raw, dt.date):
                # если перепутали порядок аргументов — игнорируем этот элемент
                continue
            name = (raw or "").strip()
            if not name:
                continue
            cur.execute("""
            INSERT INTO watches (chat_id, day, name_en)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING;
            """, (chat_id, day, name))
            cnt += cur.rowcount
        con.commit()
    return cnt


def add_watch(chat_id: int, name_en: str, day: dt.date) -> int:
    # ВАЖНО: правильный порядок аргументов
    return add_watches(chat_id, day, [name_en])


def remove_watch(chat_id: int, day: dt.date, name_en: str) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM watches WHERE chat_id=%s AND day=%s AND name_en=%s",
                    (chat_id, day, name_en.strip()))
        n = cur.rowcount
        con.commit()
        return n


def list_today(chat_id: int, day: dt.date) -> List[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "SELECT name_en FROM watches WHERE chat_id=%s AND day=%s ORDER BY name_en",
            (chat_id, day),
        )
        return [r[0] for r in cur.fetchall()]


# ---------- events cache ----------

def get_events_cache(ds: dt.date) -> Optional[dict]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT json FROM events_cache WHERE ds=%s", (ds,))
        row = cur.fetchone()
        return row[0] if row else None


def set_events_cache(ds: dt.date, data: dict) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        INSERT INTO events_cache (ds, json, created_at)
        VALUES (%s, %s, now())
        ON CONFLICT (ds) DO UPDATE
           SET json = EXCLUDED.json, created_at = now();
        """, (ds, data))
        con.commit()
