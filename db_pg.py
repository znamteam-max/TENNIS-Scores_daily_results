# db_pg.py
# Neon Postgres обёртка (использует переменную окружения POSTGRES_URL)
# Таблицы: users, watches, player_names
import os
import psycopg
import datetime as dt
from typing import Iterable, Optional, Tuple, List
from zoneinfo import ZoneInfo

DEFAULT_TZ = os.getenv("APP_TZ", "Europe/Helsinki")

DSN = os.getenv("POSTGRES_URL")
if not DSN:
    raise RuntimeError("POSTGRES_URL is not set")

def _conn():
    # autocommit=True — удобно для простых upsert'ов и DDL
    return psycopg.connect(DSN, autocommit=True)

# ---------------- СХЕМА ----------------

def ensure_schema() -> None:
    """Создаёт таблицы, если их ещё нет."""
    ddl = """
    create table if not exists users(
        chat_id     bigint primary key,
        tz          text not null default 'Europe/Helsinki',
        created_at  timestamptz not null default now()
    );

    create table if not exists player_names(
        -- каноничное английское имя игрока
        name_en     text primary key,
        -- как писать на русском
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
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(ddl)

# --------------- USERS / TZ ----------------

def ensure_user(chat_id: int, tz: Optional[str] = None) -> None:
    """Гарантирует наличие пользователя; при переданном tz — обновляет его."""
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
    """Возвращает таймзону пользователя или DEFAULT_TZ, если пользователя ещё нет."""
    with _conn() as con, con.cursor() as cur:
        cur.execute("select tz from users where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else DEFAULT_TZ

def set_tz(chat_id: int, tz: str) -> None:
    """Устанавливает таймзону пользователю (создаёт пользователя при необходимости)."""
    ensure_user(chat_id, tz=tz)

def today_for_chat(chat_id: int) -> dt.date:
    """Дата «сегодня» в таймзоне пользователя."""
    tz = get_tz(chat_id)
    try:
        now_local = dt.datetime.now(ZoneInfo(tz))
    except Exception:
        now_local = dt.datetime.now(ZoneInfo(DEFAULT_TZ))
    return now_local.date()

# --------- СЛОВАРЬ ИМЁН ИГРОКОВ (EN <-> RU) ---------

def save_player_locale(name_en: str, name_ru: Optional[str]) -> None:
    """Сохранить/обновить русскую запись для игрока."""
    name_en = name_en.strip()
    name_ru = name_ru.strip() if name_ru else None
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into player_names(name_en, name_ru)
            values (%s, %s)
            on conflict (name_en) do update set name_ru = excluded.name_ru
            """,
            (name_en, name_ru),
        )

def get_player_ru(name_en: str) -> Optional[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "select name_ru from player_names where lower(name_en)=lower(%s)",
            (name_en,),
        )
        row = cur.fetchone()
        return row[0] if row else None

# ---------------- WATCH-ЛИСТ ----------------

def add_watches(chat_id: int, day: dt.date, names_en: Iterable[str]) -> int:
    """Добавить нескольких игроков в наблюдение на день.
    Возвращает количество добавленных/обновлённых записей.
    """
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

def list_watches(chat_id: int, day: dt.date) -> List[Tuple[str, Optional[str]]]:
    """Список наблюдений на день: [(name_en, name_ru), ...]"""
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

def remove_watch(chat_id: int, day: dt.date, name_en: str) -> int:
    """Удалить игрока из наблюдения на день. Возвращает число удалённых строк (0/1)."""
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            delete from watches
            where chat_id = %s and day = %s and lower(name_en) = lower(%s)
            """,
            (chat_id, day, name_en.strip()),
        )
        return cur.rowcount
