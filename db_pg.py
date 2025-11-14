from __future__ import annotations
import os, psycopg, datetime as dt
from typing import List, Tuple

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
          label text,
          resolved text,
          source text,
          day date,
          created_at timestamptz default now(),
          primary key(chat_id,label,day)
        );
        create table if not exists cache_events(
          day date primary key,
          payload jsonb,
          created_at timestamptz default now()
        );
        create table if not exists aliases(
          name_key text primary key,
          latin text,
          ru text
        );
        """)
        # базовые алиасы
        base = {
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
        for k, v in base.items():
            cur.execute(
              "insert into aliases(name_key,latin,ru) values(%s,%s,%s) "
              "on conflict(name_key) do nothing",
              (k, k, v)
            )

def get_tz(chat_id: int) -> str:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from users where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row else "Europe/Helsinki"

def set_tz(chat_id: int, tz: str):
    with _conn() as con, con.cursor() as cur:
        cur.execute("insert into users(chat_id,tz) values(%s,%s) "
                    "on conflict(chat_id) do update set tz=excluded.tz", (chat_id, tz))

def ensure_user(chat_id: int):
    with _conn() as con, con.cursor() as cur:
        cur.execute("insert into users(chat_id) values(%s) on conflict do nothing", (chat_id,))

def norm_key(s: str) -> str:
    s = (s or "").lower()
    keep = []
    for ch in s:
        if "a" <= ch <= "z" or ch in " -'":
            keep.append(ch)
    return "".join(keep).strip()

def set_alias(latin: str, ru: str):
    with _conn() as con, con.cursor() as cur:
        key = norm_key(latin)
        cur.execute(
          "insert into aliases(name_key,latin,ru) values(%s,%s,%s) "
          "on conflict(name_key) do update set latin=excluded.latin, ru=excluded.ru",
          (key, latin, ru)
        )

def ru_name_for(name: str) -> tuple[str, bool]:
    # return (ru, known?)  known=False -> надо спросить у пользователя
    if name and any("а" <= c <= "я" for c in name.lower()):
        return name, True
    key = norm_key(name)
    with _conn() as con, con.cursor() as cur:
        cur.execute("select ru from aliases where name_key=%s", (key,))
        row = cur.fetchone()
        if row:
            return row[0], True
    # нет в базе
    return name, False

def add_watch(chat_id: int, label_ru: str, day: dt.date, source: str = "sofascore"):
    with _conn() as con, con.cursor() as cur:
        cur.execute("insert into watches(chat_id,label,resolved,source,day) "
                    "values(%s,%s,%s,%s,%s) on conflict do nothing",
                    (chat_id, label_ru, norm_key(label_ru), source, day))

def delete_watch(chat_id: int, label_ru: str, day: dt.date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and label=%s and day=%s",
                    (chat_id, label_ru, day))
        return cur.rowcount

def clear_today(chat_id: int, day: dt.date) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and day=%s", (chat_id, day))
        return cur.rowcount

def list_today(chat_id: int, day: dt.date) -> List[tuple[str,str,str]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select label,resolved,source from watches "
                    "where chat_id=%s and day=%s order by created_at", (chat_id, day))
        return list(cur.fetchall())

def set_events_cache(day: dt.date, events: list):
    with _conn() as con, con.cursor() as cur:
        cur.execute("insert into cache_events(day,payload) values(%s,%s) "
                    "on conflict(day) do update set payload=excluded.payload, created_at=now()",
                    (day, psycopg.types.json.Json(events)))

def get_events_cache(day: dt.date) -> list:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select payload from cache_events where day=%s", (day,))
        row = cur.fetchone()
        return row[0] if row else []
