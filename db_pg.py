from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import psycopg

POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")


def _conn():
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is not set")
    return psycopg.connect(POSTGRES_URL, autocommit=True)


def ensure_schema() -> None:
    sql = """
    create table if not exists chats (
        chat_id bigint primary key,
        tz text not null default 'Europe/Tallinn'
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

    create table if not exists events_cache (
        ds date primary key
    );

    alter table events_cache add column if not exists data jsonb;
    alter table events_cache add column if not exists updated_at timestamptz not null default now();

    create table if not exists user_states (
        chat_id bigint primary key,
        state text not null,
        payload jsonb not null default '{}'::jsonb,
        updated_at timestamptz not null default now()
    );

    create table if not exists match_watches (
        chat_id bigint not null,
        day date not null,
        event_id bigint not null,
        category text not null,
        tournament_name text not null,
        home_name text not null,
        away_name text not null,
        start_ts bigint,
        primary key (chat_id, day, event_id)
    );

    alter table match_watches add column if not exists notified_at timestamptz;

    create table if not exists result_cards (
        card_id text primary key,
        chat_id bigint not null,
        event_id bigint,
        event_data jsonb not null,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now()
    );

    create table if not exists match_odds (
        event_id bigint primary key,
        day date not null,
        home_odds double precision,
        away_odds double precision,
        source text not null default 'unknown',
        raw jsonb not null default '{}'::jsonb,
        fetched_at timestamptz not null default now()
    );

    create table if not exists odds_refreshes (
        day date primary key,
        refreshed_at timestamptz not null default now()
    );

    create table if not exists daily_summaries (
        summary_key text primary key,
        day date not null,
        tour_group text not null,
        tournament_name text not null,
        tournament_status text not null,
        stage text not null default '',
        sent_at timestamptz not null default now()
    );

    create table if not exists summary_reviews (
        summary_id text primary key,
        chat_id text not null,
        message_id bigint,
        source_chat_id bigint,
        day date not null,
        tour_group text not null,
        tournament_name text not null,
        tournament_status text not null,
        stage text not null default '',
        event_data jsonb not null,
        overrides jsonb not null default '{}'::jsonb,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now()
    );
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql)


def ping_db() -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select 1")
        return True


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


def set_alias(en_full: str, ru: str) -> None:
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
    with _conn() as con, con.cursor() as cur:
        cur.execute("select en_full from pending_alias where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return None
        en_full = row[0]
        cur.execute("delete from pending_alias where chat_id=%s", (chat_id,))
        return en_full


def add_watch(chat_id: int, name_en: str, day: dt.date) -> None:
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


def set_events_cache(ds: dt.date, data: Dict[str, Any]) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into events_cache (ds, data, updated_at)
            values (%s, %s::jsonb, now())
            on conflict (ds) do update
            set data=excluded.data, updated_at=now()
            """,
            (ds, json.dumps(data)),
        )


def get_events_cache(ds: dt.date) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select data from events_cache where ds=%s", (ds,))
        row = cur.fetchone()
        return row[0] if row else None


def set_state(chat_id: int, state: str, payload: Optional[Dict[str, Any]] = None) -> None:
    payload = payload or {}
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into user_states (chat_id, state, payload, updated_at)
            values (%s, %s, %s::jsonb, now())
            on conflict (chat_id) do update
            set state=excluded.state, payload=excluded.payload, updated_at=now()
            """,
            (chat_id, state, json.dumps(payload)),
        )


def get_state(chat_id: int) -> Tuple[Optional[str], Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select state, payload from user_states where chat_id=%s", (chat_id,))
        row = cur.fetchone()
        if not row:
            return (None, {})
        return (row[0], row[1] or {})


def clear_state(chat_id: int) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute("delete from user_states where chat_id=%s", (chat_id,))


def add_match_watch(chat_id: int, day: dt.date, match_row: Dict[str, Any]) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into match_watches (
                chat_id, day, event_id, category, tournament_name,
                home_name, away_name, start_ts
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            """,
            (
                chat_id,
                day,
                int(match_row["event_id"]),
                str(match_row["category"]),
                str(match_row["tournament_name"]),
                str(match_row["home_name"]),
                str(match_row["away_name"]),
                match_row.get("start_ts"),
            ),
        )
        return cur.rowcount > 0


def remove_match_watch(chat_id: int, day: dt.date, event_id: int) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            delete from match_watches
            where chat_id=%s and day=%s and event_id=%s
            """,
            (chat_id, day, int(event_id)),
        )
        return cur.rowcount > 0


def list_match_watches(chat_id: int, day: dt.date) -> List[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select event_id, category, tournament_name, home_name, away_name, start_ts
            from match_watches
            where chat_id=%s and day=%s
            order by tournament_name, start_ts nulls last, home_name, away_name
            """,
            (chat_id, day),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "event_id": r[0],
                    "category": r[1],
                    "tournament_name": r[2],
                    "home_name": r[3],
                    "away_name": r[4],
                    "start_ts": r[5],
                }
            )
        return rows


def list_pending_match_watch_days() -> List[dt.date]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select distinct day
            from match_watches
            where notified_at is null
              and day >= (current_date - interval '3 days')
            order by day
            """
        )
        return [r[0] for r in cur.fetchall()]


def list_pending_match_watches(day: dt.date) -> List[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select chat_id, event_id, category, tournament_name, home_name, away_name, start_ts
            from match_watches
            where day=%s
              and notified_at is null
            order by chat_id, tournament_name, start_ts nulls last, home_name, away_name
            """,
            (day,),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "chat_id": r[0],
                    "event_id": r[1],
                    "category": r[2],
                    "tournament_name": r[3],
                    "home_name": r[4],
                    "away_name": r[5],
                    "start_ts": r[6],
                }
            )
        return rows


def mark_match_notified(chat_id: int, day: dt.date, event_id: int) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            update match_watches
            set notified_at=now()
            where chat_id=%s
              and day=%s
              and event_id=%s
              and notified_at is null
            """,
            (chat_id, day, int(event_id)),
        )
        return cur.rowcount > 0


def save_result_card(card_id: str, chat_id: int, event: Dict[str, Any]) -> None:
    card_id = (card_id or "").strip()
    if not card_id:
        return
    event_id = event.get("event_id")
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into result_cards (card_id, chat_id, event_id, event_data, updated_at)
            values (%s, %s, %s, %s::jsonb, now())
            on conflict (card_id) do update
            set chat_id=excluded.chat_id,
                event_id=excluded.event_id,
                event_data=excluded.event_data,
                updated_at=now()
            """,
            (card_id, int(chat_id), int(event_id) if event_id is not None else None, json.dumps(event, ensure_ascii=False)),
        )


def mark_event_notified(day: dt.date, event_id: int) -> int:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            update match_watches
            set notified_at=now()
            where day=%s
              and event_id=%s
              and notified_at is null
            """,
            (day, int(event_id)),
        )
        return cur.rowcount


def get_result_card(chat_id: int, card_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select event_data
            from result_cards
            where chat_id=%s and card_id=%s
            """,
            (int(chat_id), (card_id or "").strip()),
        )
        row = cur.fetchone()
        return row[0] if row else None


def update_result_card(chat_id: int, card_id: str, event: Dict[str, Any]) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            update result_cards
            set event_data=%s::jsonb, updated_at=now()
            where chat_id=%s and card_id=%s
            """,
            (json.dumps(event, ensure_ascii=False), int(chat_id), (card_id or "").strip()),
        )
        return cur.rowcount > 0


def odds_refresh_due(day: dt.date, min_age_minutes: int) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select refreshed_at from odds_refreshes where day=%s", (day,))
        row = cur.fetchone()
        if not row:
            return True
        cur.execute("select now() - %s > (%s || ' minutes')::interval", (row[0], int(min_age_minutes)))
        due = cur.fetchone()
        return bool(due and due[0])


def mark_odds_refresh(day: dt.date) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into odds_refreshes (day, refreshed_at)
            values (%s, now())
            on conflict (day) do update set refreshed_at=now()
            """,
            (day,),
        )


def upsert_match_odds(event_id: int, day: dt.date, home_odds: float, away_odds: float, source: str, raw: Dict[str, Any]) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into match_odds (event_id, day, home_odds, away_odds, source, raw, fetched_at)
            values (%s, %s, %s, %s, %s, %s::jsonb, now())
            on conflict (event_id) do update
            set day=excluded.day,
                home_odds=excluded.home_odds,
                away_odds=excluded.away_odds,
                source=excluded.source,
                raw=excluded.raw,
                fetched_at=now()
            """,
            (int(event_id), day, float(home_odds), float(away_odds), source, json.dumps(raw, ensure_ascii=False)),
        )


def get_match_odds_map(event_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    ids = [int(x) for x in event_ids if x is not None]
    if not ids:
        return {}
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select event_id, home_odds, away_odds, source, raw, fetched_at
            from match_odds
            where event_id = any(%s::bigint[])
            """,
            (ids,),
        )
        return {
            int(row[0]): {
                "home_odds": row[1],
                "away_odds": row[2],
                "source": row[3],
                "raw": row[4] or {},
                "fetched_at": row[5],
            }
            for row in cur.fetchall()
        }


def is_daily_summary_sent(summary_key: str) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute("select 1 from daily_summaries where summary_key=%s", ((summary_key or "").strip(),))
        return cur.fetchone() is not None


def mark_daily_summary_sent(summary_key: str, day: dt.date, tour_group: str, tournament_name: str, tournament_status: str, stage: str) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into daily_summaries (
                summary_key, day, tour_group, tournament_name, tournament_status, stage, sent_at
            )
            values (%s, %s, %s, %s, %s, %s, now())
            on conflict (summary_key) do nothing
            """,
            (
                (summary_key or "").strip(),
                day,
                tour_group or "",
                tournament_name or "",
                tournament_status or "",
                stage or "",
            ),
        )


def save_summary_review(
    summary_id: str,
    chat_id: int | str,
    source_chat_id: int,
    message_id: Optional[int],
    day: dt.date,
    tour_group: str,
    tournament_name: str,
    tournament_status: str,
    stage: str,
    events: List[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            insert into summary_reviews (
                summary_id, chat_id, source_chat_id, message_id, day, tour_group,
                tournament_name, tournament_status, stage, event_data, overrides, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
            on conflict (summary_id) do update
            set chat_id=excluded.chat_id,
                source_chat_id=excluded.source_chat_id,
                message_id=excluded.message_id,
                day=excluded.day,
                tour_group=excluded.tour_group,
                tournament_name=excluded.tournament_name,
                tournament_status=excluded.tournament_status,
                stage=excluded.stage,
                event_data=excluded.event_data,
                overrides=excluded.overrides,
                updated_at=now()
            """,
            (
                summary_id,
                str(chat_id),
                int(source_chat_id),
                int(message_id) if message_id else None,
                day,
                tour_group or "",
                tournament_name or "",
                tournament_status or "",
                stage or "",
                json.dumps(events, default=str),
                json.dumps(overrides or {}),
            ),
        )


def set_summary_review_message(summary_id: str, message_id: int) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "update summary_reviews set message_id=%s, updated_at=now() where summary_id=%s",
            (int(message_id), (summary_id or "").strip()),
        )


def is_summary_review_pending(day: dt.date, tour_group: str, tournament_name: str, tournament_status: str, stage: str) -> bool:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select 1
            from summary_reviews
            where day=%s
              and tour_group=%s
              and tournament_name=%s
              and tournament_status=%s
              and stage=%s
            limit 1
            """,
            (
                day,
                tour_group or "",
                tournament_name or "",
                tournament_status or "",
                stage or "",
            ),
        )
        return cur.fetchone() is not None


def get_summary_review(summary_id: str) -> Optional[Dict[str, Any]]:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            """
            select summary_id, chat_id, message_id, source_chat_id, day, tour_group,
                   tournament_name, tournament_status, stage, event_data, overrides
            from summary_reviews
            where summary_id=%s
            """,
            ((summary_id or "").strip(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "summary_id": row[0],
            "chat_id": row[1],
            "message_id": row[2],
            "source_chat_id": row[3],
            "day": row[4],
            "tour_group": row[5],
            "tournament_name": row[6],
            "tournament_status": row[7],
            "stage": row[8],
            "events": row[9] or [],
            "overrides": row[10] or {},
        }


def update_summary_review_overrides(summary_id: str, overrides: Dict[str, Any]) -> None:
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "update summary_reviews set overrides=%s::jsonb, updated_at=now() where summary_id=%s",
            (json.dumps(overrides or {}), (summary_id or "").strip()),
        )
