# db_pg.py
# Neon Postgres обёртка (использует переменную окружения POSTGRES_URL / DATABASE_URL)
# Таблицы: users, player_names, watches, schedules
from __future__ import annotations

import os
import json
import datetime as dt
from typing import Iterable, Optional, Tuple, List, Dict, Any
from zoneinfo import ZoneInfo

import psycopg

__all__ = [
    # схема/коннект
    "ensure_schema",
    # пользователи и часовой пояс
    "ensure_user",
    "get_tz",
    "set_tz",
    "today_for_chat",
    # локализация имён
    "save_player_locale",
    "set_alias",
    "add_player_alias",
    "get_player_ru",
    "player_ru",
    "ru_name_for",
    # watch-лист
    "add_watch",
    "add_watches",
    "list_watches",
    "watches_for",
    "list_today",
    "remove_watch",
    "delete_watch",
    "clear_today",
    "clear_watches",
    "delete_all_watches",
    # кэш расписания
    "cache_schedule",
    "read_schedule",
]

# ----------- CONFIG -----------

DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")

DSN = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
if not DSN:
    raise RuntimeError("POSTGRES_URL (или DATABASE_URL) не задан")

def _conn():
    return psycopg.connect(DSN, autocommit=True)

# ----------- SCHEMA -----------

def ensure_schema() -> None:
    ddl = """
    create table if not exists users(
        chat_id     bigint primary key,
        tz          text not null default 'Europe/Helsinki',
        created_at  timestamptz not null default now()
    );

    create table if not exists player_names(
        name_en     text primary key,
        name_ru     text
    );

    create table if not exists watches(
        chat_id     bigint not null references users(chat_id) on delete cascade,
        day         date   not null,
        name_en     text   not null,
        name_ru     text,
        created_at  timestamptz not null default now(),
        constraint watches_pk primary key (chat_id, day, name_en)
    );

    create index if not exists watches_day_idx on watches(day);

    create table if not exists schedules(
        day         date primary key,
        payload     jsonb not null,
        created_at  timestamptz not null default now()
    );
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(ddl)

# ----------- USERS / TZ -----------

def ensure_user(chat_id: int, tz: Optional[str] = None) -> None:
    with _conn() as con, con.cursor() as cur:
        if tz:
            cur.execute(
                """
                insert into users(chat_id, tz) values (%s, %s)
                on conflict (chat_id) do update set tz = excluded.tz
                """,
                (chat_id, tz),
            )
        else:
            cur.execute(
                "insert into users(chat_id) values (%s) on conflict (chat_id) do nothing",
                (chat_id,),
            )

def get_tz(chat_id: int) -> str:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from users where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else DEFAULT_TZ

def set_tz(chat_id: int, tz: str) -> None:
    ensure_user(chat_id, tz=tz)

def today_for_chat(chat_id: int) -> dt.date:
    tz = get_tz(chat_id)
    try:
        now_local = dt.datetime.now(ZoneInfo(tz))
    except Exception:
        now_local = dt.datetime.now(ZoneInfo(DEFAULT_TZ))
    return now_local.date()

# ----------- PLAYER NAMES (EN <-> RU) -----------

def save_player_locale(name_en: str, name_ru: Optional[str]) -> None:
    name_en = (name_en or "").strip()
    name_ru = (name_ru or None)
    if name_ru:
        name_ru = name_ru.strip()
    if not name_en:
        return
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into player_names(name_en, name_ru)
            values (%s, %s)
            on conflict (name_en) do update set name_ru = excluded.name_ru
            """,
            (name_en, name_ru),
        )

def set_alias(name_en: str, name_ru: Optional[str]) -> None:
    save_player_locale(name_en, name_ru)

# удобные алиасы на случай других импортов
def add_player_alias(name_en: str, name_ru: Optional[str]) -> None:
    save_player_locale(name_en, name_ru)

def get_player_ru(name_en: str) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "select name_ru from player_names where lower(name_en)=lower(%s)",
            (name_en,),
        )
        row = cur.fetchone()
        return row[0] if row else None

def player_ru(name_en: str) -> Optional[str]:
    return get_player_ru(name_en)

def _has_cyrillic(s: str) -> bool:
    return any("А" <= ch <= "я" or ch in ("ё", "Ё") for ch in s)

def ru_name_for(name: str) -> Optional[str]:
    if not name:
        return None
    name = name.strip()
    if _has_cyrillic(name):
        return name
    return get_player_ru(name)

# ----------- WATCH LIST -----------

def add_watches(chat_id: int, day: dt.date, names_en: Iterable[str]) -> int:
    ensure_user(chat_id)
    added = 0
    with _conn() as con, con.cursor() as cur:
        for raw in names_en:
            name_en = (raw or "").strip()
            if not name_en:
                continue
            name_ru = get_player_ru(name_en)
            cur.execute(
                """
                insert into watches(chat_id, day, name_en, name_ru)
                values (%s, %s, %s, %s)
                on conflict (chat_id, day, name_en) do update
                    set name_ru = coalesce(excluded.name_ru, watches.name_ru)
                """,
                (chat_id, day, name_en, name_ru),
            )
            added += 1
    return added

def add_watch(chat_id: int, day: dt.date, name_en: str) -> int:
    return add_watches(chat_id, day, [name_en])

def list_watches(chat_id: int, day: dt.date) -> List[Tuple[str, Optional[str]]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select name_en, name_ru
            from watches
            where chat_id = %s and day = %s
            order by lower(name_en)
            """,
            (chat_id, day),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]

# алиас: то же самое, просто под другим именем
def watches_for(chat_id: int, day: dt.date) -> List[Tuple[str, Optional[str]]]:
    return list_watches(chat_id, day)

def list_today(chat_id: int) -> List[Tuple[str, Optional[str]]]:
    return list_watches(chat_id, today_for_chat(chat_id))

def remove_watch(chat_id: int, day: dt.date, name_en: str) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            delete from watches
            where chat_id = %s and day = %s and lower(name_en) = lower(%s)
            """,
            (chat_id, day, name_en.strip()),
        )
        return cur.rowcount

def delete_watch(chat_id: int, day: dt.date, name_en: str) -> int:
    return remove_watch(chat_id, day, name_en)

def clear_today(chat_id: int, day: dt.date) -> int:
    """Удалить все наблюдения пользователя на указанный день. Возвращает кол-во удалённых строк."""
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "delete from watches where chat_id=%s and day=%s",
            (chat_id, day),
        )
        return cur.rowcount

# дополнительные алиасы, чтобы больше не ловить ImportError по названиям
def clear_watches(chat_id: int, day: dt.date) -> int:
    return clear_today(chat_id, day)

def delete_all_watches(chat_id: int, day: dt.date) -> int:
    return clear_today(chat_id, day)

# ----------- SCHEDULE CACHE (per day) -----------

def cache_schedule(day: dt.date, payload: Dict[str, Any]) -> None:
    js = json.dumps(payload, ensure_ascii=False)
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into schedules(day, payload)
            values (%s, %s::jsonb)
            on conflict (day) do update set
                payload = excluded.payload,
                created_at = now()
            """,
            (day, js),
        )

def read_schedule(day: dt.date) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select payload from schedules where day=%s", (day,))
        row = cur.fetchone()
        if not row:
            return None
        try:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        except Exception:
            return None
