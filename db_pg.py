from __future__ import annotations

import os
import json
import datetime as dt
from typing import Optional, List, Tuple, Dict, Any

import psycopg

POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")


def _conn():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(POSTGRES_URL, autocommit=True)


def ensure_schema() -> None:
    """
    Создаёт таблицы при отсутствии и выполняет мягкие миграции схемы
    (добавление недостающих колонок, переименование старых в актуальные).
    Идempotent: безопасно выполнять на каждом старте.
    """
    with _conn() as con, con.cursor() as cur:
        # Базовые таблицы (создадим минимум, без спорных NOT NULL, чтобы не упасть на старых схемах)
        cur.execute("""
        create table if not exists chats (
            chat_id bigint primary key,
            tz text not null default 'Europe/Berlin'
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
        -- создаём минимальную оболочку events_cache, без колонок: добавим ниже с IF NOT EXISTS
        create table if not exists events_cache (
            ds date primary key
        );
        """)

        _migrate_events_cache(cur)


def _migrate_events_cache(cur) -> None:
    """
    Приводит таблицу events_cache к виду:
      ds date primary key,
      data jsonb not null,
      updated_at timestamptz not null default now()
    - добавляет недостающие колонки,
    - если ранее была колонка с другим именем (events/payload/json/value/content) — переименует в data,
    - при необходимости приводит тип к jsonb.
    """
    cur.execute("""
        select column_name, data_type
        from information_schema.columns
        where table_schema = current_schema()
          and table_name = 'events_cache'
    """)
    cols = {name: dtype for (name, dtype) in cur.fetchall()}

    # 1) Если вместо data раньше была колонка с другим названием — переименуем
    if "data" not in cols:
        for old in ("events", "payload", "json", "value", "content"):
            if old in cols:
                cur.execute(f"alter table events_cache rename column {old} to data;")
                break

    # 2) Добавим data, если всё ещё нет
    cur.execute("alter table events_cache add column if not exists data jsonb;")

    # 3) Приведём тип data к jsonb (на случай, если это text/json)
    # Старательно и безопасно: если уже jsonb — оператор ничего не меняет.
    try:
        cur.execute("""
            alter table events_cache
            alter column data type jsonb
            using (
                case
                    when pg_typeof(data) = 'jsonb'::regtype then data
                    when pg_typeof(data) = 'json'::regtype  then data::jsonb
                    else to_jsonb(data)
                end
            );
        """)
    except Exception:
        # В крайнем случае оставляем как есть (вставка ниже всё равно пойдёт через ::jsonb)
        pass

    # 4) Добавим updated_at
    cur.execute("""
        alter table events_cache
        add column if not exists updated_at timestamptz not null default now();
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
    en_full = (en_full or "").strip()
    ru = (ru or "").strip()
    if not en_full or not ru:
        return
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into name_aliases (en_full, ru)
            values (%s, %s)
            on conflict (en_full) do update set ru=excluded.ru
        """, (en_full, ru))


def ru_name_for(en_full: str) -> Optional[Tuple[str, bool]]:
    """
    Возвращает (ru, True) если RU-алиас есть.
    Возвращает None, если записи вообще нет — в этом случае боту нужно спросить русскую запись у пользователя.
    """
    en_full = (en_full or "").strip()
    if not en_full:
        return None
    with _conn() as con, con.cursor() as cur:
        cur.execute("select ru from name_aliases where en_full=%s", (en_full,))
        row = cur.fetchone()
        if row:
            return (row[0], True)
        return None


def set_pending_alias(chat_id: int, en_full: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into pending_alias (chat_id, en_full)
            values (%s, %s)
            on conflict (chat_id) do update set en_full=excluded.en_full
        """, (chat_id, (en_full or "").strip()))


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
        """, (chat_id, day, (name_en or "").strip()))


def remove_watch(chat_id: int, day: dt.date, name_en: str) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from watches where chat_id=%s and day=%s and name_en=%s",
                    (chat_id, day, (name_en or "").strip()))
        return cur.rowcount > 0


def list_today(chat_id: int, day: dt.date) -> List[str]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            select name_en from watches where chat_id=%s and day=%s order by name_en
        """, (chat_id, day))
        return [r[0] for r in cur.fetchall()]


# ---------- events cache ----------
def set_events_cache(ds: dt.date, data: Dict[str, Any]) -> None:
    """
    Записывает кэш событий на дату. Используем ::jsonb, чтобы гарантировать тип даже если драйвер
    передаёт строку. Перед вставкой миграция ensure_schema() гарантирует наличие колонки data.
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute("""
            insert into events_cache (ds, data, updated_at)
            values (%s, %s::jsonb, now())
            on conflict (ds) do update set data=excluded.data, updated_at=now()
        """, (ds, json.dumps(data or {})))


def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select data from events_cache where ds=%s", (ds,))
        row = cur.fetchone()
        if not row:
            return None
        val = row[0]
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return None
        return val
