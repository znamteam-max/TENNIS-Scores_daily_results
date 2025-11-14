# db_pg.py
from __future__ import annotations
import os, psycopg, datetime as dt
from typing import List, Tuple, Optional

PG_URL = os.getenv("POSTGRES_URL")

def _conn():
    if not PG_URL:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(PG_URL, autocommit=True)

def ensure_schema():
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
        create table if not exists users(
          chat_id bigint primary key,
          tz text default 'Europe/Helsinki'
        );
        create table if not exists watches(
          chat_id bigint,
          label text,           -- отображаемое имя (обычно RU)
          resolved text,        -- нормализованная форма (ключ)
          source text,          -- 'sofascore' / 'manual'
          day date,
          created_at timestamptz default now(),
          primary key(chat_id, label, day)
        );
        create table if not exists cache_events(
          day date primary key,
          payload jsonb,        -- events[]
          created_at timestamptz default now()
        );
        create table if not exists aliases(
          name_key text primary key,  -- нормализованный латинский ключ
          latin text,
          ru text
        );
        """)
        # дефолтные алиасы (чтобы не начинать с пустоты)
        defaults = {
            "jannik sinner": "Янник Синнер",
            "alexander zverev": "Александр Зверев",
            "daniil medvedev": "Даниил Медведев",
            "andrey rublev": "Андрей Рублёв",
            "alex de minaur": "Алекс де Минор",
            "lorenzo musetti": "Лоренцо Музетти",
            "stefanos tsitsipas": "Стефанос Циципас",
            "taylor fritz": "Тейлор Фриц",
            "frances tiafoe": "Фрэнсис Тиафо",
            "karen khachanov": "Карен Хачанов",
            "novak djokovic": "Новак Джокович",
            "carlos alcaraz": "Карлос Алькарас",
            "stan wawrinka": "Стэн Вавринка",
            "reilly opelka": "Рейлли Опелка",
        }
        for k, v in defaults.items():
            cur.execute(
                "insert into aliases(name_key, latin, ru) values(%s,%s,%s) "
                "on conflict (name_key) do nothing",
                (k, k, v)
            )

def ensure_user(chat_id: int):
    with _conn() as con, con.cursor() as cur:
        cur.execute("insert into users(chat_id) values(%s) on conflict (chat_id) do nothing", (chat_id,))

def set_tz(chat_id: int, tz: str):
    with _conn() as con, con.cursor() as cur:
        cur.execute("update users set tz=%s where chat_id=%s", (tz, chat_id))

def get_tz(chat_id: int) -> str:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from users where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return (row[0] if row else "Europe/Helsinki")

def norm_key(name: str) -> str:
    # грубая нормализация под ключ
    s = (name or "").lower()
    keep = []
    for ch in s:
        if "a" <= ch <= "z" or ch in " -'":
            keep.append(ch)
    return "".join(keep).strip()

def set_alias(latin: str, ru: str):
    with _conn() as con, con.cursor() as cur:
        key = norm_key(latin)
        cur.execute(
            "insert into aliases(name_key, latin, ru) values(%s,%s,%s) "
            "on conflict (name_key) do update set latin=excluded.latin, ru=excluded.ru",
            (key, latin, ru)
        )

def ru_name_for(name: str) -> str:
    # если пришло на русском — вернём как есть
    if any("а" <= ch <= "я" for ch in (name or "").lower()):
        return name
    key = norm_key(name)
    with _conn() as con, con.cursor() as cur:
        cur.execute("select ru from aliases where name_key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else name  # если нет маппинга — показываем как ввели

def add_watch(chat_id: int, label: str, source: str, day: dt.date):
    # label уже должен быть RU
    resolved = norm_key(label)
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "insert into watches(chat_id,label,resolved,source,day) values(%s,%s,%s,%s,%s) "
            "on conflict do nothing",
            (chat_id, label, resolved, source, day)
        )

def delete_watch(chat_id: int, label: str, day: dt.date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and label=%s and day=%s", (chat_id, label, day))
        return cur.rowcount

def clear_today(chat_id: int, day: dt.date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and day=%s", (chat_id, day))
        return cur.rowcount

def list_today(chat_id: int, day: dt.date) -> List[Tuple[str, str, str]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "select label,resolved,source from watches where chat_id=%s and day=%s order by created_at",
            (chat_id, day)
        )
        return list(cur.fetchall())

def set_events_cache(day: dt.date, events: list):
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "insert into cache_events(day,payload) values(%s,%s) "
            "on conflict(day) do update set payload=excluded.payload, created_at=now()",
            (day, psycopg.types.json.Json(events))
        )

def get_events_cache(day: dt.date) -> list:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select payload from cache_events where day=%s", (day,))
        row = cur.fetchone()
        return row[0] if row else []
