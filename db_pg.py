# db_pg.py
from __future__ import annotations

import os
import json
import datetime as dt
from typing import Optional, List, Tuple, Dict, Any

import psycopg


# --------------------------------------------------------------------
#  Конфиг подключения
# --------------------------------------------------------------------
POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")


def _conn():
    """
    Открывает соединение к БД с autocommit=True.
    Требуется POSTGRES_URL в окружении.
    """
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(POSTGRES_URL, autocommit=True)


# --------------------------------------------------------------------
#  Схема и служебные
# --------------------------------------------------------------------
def ensure_schema() -> None:
    """
    Создаёт/мигрирует таблицы:
      - chats(chat_id, tz)
      - name_aliases(en_full, ru)
      - pending_alias(chat_id, en_full)
      - watches(chat_id, day, name_en)
      - events_cache(ds, data, updated_at)

    Важно: колонка events_cache.data должна быть jsonb.
    """
    sql = """
    create table if not exists chats (
        chat_id bigint primary key,
        tz      text not null default 'Europe/London'
    );

    create table if not exists name_aliases (
        en_full text primary key,
        ru      text not null
    );

    create table if not exists pending_alias (
        chat_id bigint primary key,
        en_full text not null
    );

    create table if not exists watches (
        chat_id bigint not null,
        day     date   not null,
        name_en text   not null,
        primary key (chat_id, day, name_en)
    );

    create table if not exists events_cache (
        ds         date primary key
    );

    -- миграции на случай старых схем:
    alter table events_cache
        add column if not exists data jsonb;

    alter table events_cache
        add column if not exists updated_at timestamptz not null default now();
    """
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(sql)


def ping_db() -> bool:
    """ Простой пинг БД. """
    with _conn() as con, con.cursor() as cur:
        cur.execute("select 1")
        return True


# --------------------------------------------------------------------
#  Часовой пояс чата
# --------------------------------------------------------------------
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


# --------------------------------------------------------------------
#  Алиасы имён (EN → RU) + ожидание ответа от пользователя
# --------------------------------------------------------------------
def norm_key(s: str) -> str:
    """ Унификация ключа (на будущее; сейчас не используется). """
    return " ".join((s or "").strip().lower().split())


def set_alias(en_full: str, ru: str) -> None:
    """ Сохраняет/обновляет RU-алиас для полного EN-имени. """
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
    """
    Возвращает (ru, known).

    known = True  → алиас есть, ru — непустой
    known = False → алиаса нет (ru = "")

    Никогда не возвращает None — это важно для кода, который делает
    распаковку (ru, known) без дополнительной проверки.
    """
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
    """
    Ставит чат в режим ожидания RU-варианта для указанного EN-имени.
    """
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
    """
    Достаёт (и удаляет) ожидаемое EN-имя для чата.
    Если ничего не ждём — вернёт None.
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute("select en_full from pending_alias where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return None
        en_full = row[0]
        cur.execute("delete from pending_alias where chat_id=%s", (chat_id,))
        return en_full


# --------------------------------------------------------------------
#  Список наблюдаемых игроков (на день)
# --------------------------------------------------------------------
def add_watch(chat_id: int, name_en: str, day: dt.date) -> None:
    """
    Добавляет одного игрока (EN) в список «наблюдать сегодня».
    """
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
    """
    Массовое добавление игроков.
    Возвращает число реально добавленных записей (без дублей).
    """
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
    """
    Удаляет игрока из списка наблюдаемых на конкретный день.
    Возвращает True, если запись была удалена.
    """
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
    """
    Список EN-имён, отмеченных на выбранный день.
    """
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


# --------------------------------------------------------------------
#  Кэш событий (расписание Sofascore/моки)
# --------------------------------------------------------------------
def set_events_cache(ds: dt.date, data: Dict[str, Any]) -> None:
    """
    Сохраняет расписание (как JSON) на дату ds.
    Структура произвольная, обычно {"events":[...]}.
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into events_cache (ds, data, updated_at)
            values (%s, %s::jsonb, now())
            on conflict (ds) do update set data=excluded.data, updated_at=now()
            """,
            (ds, json.dumps(data)),
        )


def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]:
    """
    Возвращает словарь из кэша, если есть, иначе None.
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute("select data from events_cache where ds=%s", (ds,))
        row = cur.fetchone()
        return row[0] if row else None
