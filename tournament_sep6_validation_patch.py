from __future__ import annotations

import copy
import datetime as dt
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

import db_pg
import tournament_session_store as store


_INSTALLED = False
_DROP_STATS: Dict[tuple[int, str], Dict[str, int]] = {}
_PUBLISHED_CACHE: Dict[tuple[str, str, str, str], tuple[float, set[int]]] = {}


_BLOCKED_EVENT_WORDS = (
    "doubles",
    "double",
    "парн",
    "mixed",
    "микст",
    "junior",
    "юниор",
    "boys",
    "girls",
)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def is_adult_singles_event(event: Dict[str, Any]) -> bool:
    """Keep adult singles only in ATP/WTA tournament sessions.

    Flashscore can give singles and doubles the same shortened tournament name.
    The full league name and slash-separated participant names remain reliable
    signals, so use them before the event reaches the tournament menu.
    """
    raw = event.get("raw") or {}
    tournament_obj = raw.get("tournament") or {}
    unique = tournament_obj.get("uniqueTournament") or {}
    hay = _norm(
        " ".join(
            str(value or "")
            for value in (
                raw.get("flashscore_league"),
                raw.get("eventName"),
                event.get("season_name"),
                event.get("tournament_name"),
                tournament_obj.get("name"),
                unique.get("name"),
            )
        )
    )
    if any(word in hay for word in _BLOCKED_EVENT_WORDS):
        return False

    # Flashscore doubles participants are represented as "Player A/Player B".
    for side in ("home_name", "away_name"):
        name = str(event.get(side) or "")
        if "/" in name:
            return False
    return True


def _event_game_day(event: Dict[str, Any]) -> dt.date | None:
    try:
        timestamp = int(event.get("start_ts") or 0)
    except Exception:
        timestamp = 0
    if not timestamp:
        value = event.get("session_day") or event.get("_source_day")
        try:
            return dt.date.fromisoformat(str(value)[:10]) if value else None
        except Exception:
            return None

    source = str(event.get("tournament_source_name") or event.get("tournament_name") or "")
    profile = store.get_profile(source)
    timezone = str(event.get("tournament_timezone") or profile.get("tz") or store.DEFAULT_TZ)
    try:
        cutoff = int(event.get("tournament_cutoff_minutes", profile.get("cutoff", store.DEFAULT_CUTOFF)))
    except Exception:
        cutoff = store.DEFAULT_CUTOFF
    try:
        return (
            dt.datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
            - dt.timedelta(minutes=cutoff)
        ).date()
    except Exception:
        return None


def _stage_from_match_page(match: Dict[str, Any]) -> tuple[int, str]:
    event_id = int(match.get("event_id") or 0)
    try:
        import match_card

        # Use only the match page's own og:description parser. Do not scan the
        # entire HTML for words like "Final": related matches/navigation can
        # contain another round and caused false stages in the old full scan.
        stage = store.normalize_stage(match_card._stage_from_flashscore_page(match))  # type: ignore[attr-defined]
        return event_id, stage
    except Exception:
        return event_id, ""


def _published_event_ids_other_days(day: dt.date, group: str, tournament: str, status: str = "") -> set[int]:
    key = (day.isoformat(), str(group or ""), str(tournament or ""), str(status or ""))
    cached = _PUBLISHED_CACHE.get(key)
    if cached and time.time() - cached[0] < 30:
        return set(cached[1])

    found: set[int] = set()
    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                select sr.event_data
                  from summary_reviews sr
                 where sr.day <> %s
                   and sr.tour_group = %s
                   and sr.tournament_name = %s
                   and (%s = '' or sr.tournament_status = %s)
                   and exists (
                       select 1
                         from daily_summaries ds
                        where ds.day = sr.day
                          and ds.tour_group = sr.tour_group
                          and ds.tournament_name = sr.tournament_name
                          and ds.tournament_status = sr.tournament_status
                          and ds.stage = sr.stage
                   )
                """,
                (day, group or "", tournament or "", status or "", status or ""),
            )
            for (events,) in cur.fetchall():
                for event in events or []:
                    try:
                        event_id = int((event or {}).get("event_id") or 0)
                    except Exception:
                        event_id = 0
                    if event_id:
                        found.add(event_id)
    except Exception as exc:
        print(f"[day-validation] previous-summary lookup failed: {exc}")

    _PUBLISHED_CACHE[key] = (time.time(), set(found))
    return found


def _clean_summary_rows(rows: Iterable[Dict[str, Any]], group: str, tournament: str, status: str = "") -> List[Dict[str, Any]]:
    prepared = [event for event in rows if is_adult_singles_event(event)]
    if not prepared:
        return []

    day: dt.date | None = None
    for event in prepared:
        value = event.get("session_day")
        if value:
            try:
                day = dt.date.fromisoformat(str(value)[:10])
                break
            except Exception:
                pass
    if day is None:
        return prepared

    already_published = _published_event_ids_other_days(day, group, tournament, status)
    out: List[Dict[str, Any]] = []
    for event in prepared:
        try:
            event_id = int(event.get("event_id") or 0)
        except Exception:
            event_id = 0
        if event_id and event_id in already_published:
            print(
                "[day-validation] drop duplicate published result "
                f"day={day} event_id={event_id} {event.get('home_name')} - {event.get('away_name')}"
            )
            continue
        out.append(event)
    return out


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    old_session_events = store.session_events
    old_menu = module._matches_menu
    old_callback = module._handle_callback
    old_summary_events = module.summary_events_for_tournament
    old_summary_builder = module.build_daily_summary_for_tournament

    def singles_session_events(old_loader: Any, chat_id: int, day: dt.date, force_refresh: bool = False):
        rows = old_session_events(old_loader, chat_id, day, force_refresh)
        kept: List[Dict[str, Any]] = []
        dropped = 0
        wrong_day = 0
        for event in rows:
            if not is_adult_singles_event(event):
                dropped += 1
                continue
            actual_day = _event_game_day(event)
            if actual_day is not None and actual_day != day:
                wrong_day += 1
                continue
            event["session_day"] = day.isoformat()
            kept.append(event)
        _DROP_STATS[(int(chat_id), day.isoformat())] = {
            "non_singles": dropped,
            "wrong_day": wrong_day,
        }
        return kept

    store.session_events = singles_session_events
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    def summary_events(events, group, tournament, status=""):
        rows = old_summary_events(events, group, tournament, status)
        return _clean_summary_rows(rows, group, tournament, status)

    module.summary_events_for_tournament = summary_events

    def summary_builder(day, events, group, tournament, status="", overrides=None):
        cleaned: List[Dict[str, Any]] = []
        for event in events:
            if not is_adult_singles_event(event):
                continue
            actual_day = _event_game_day(event)
            if actual_day is not None and actual_day != day:
                continue
            row = copy.deepcopy(event)
            row["session_day"] = day.isoformat()
            cleaned.append(row)
        # Important: the older stage-aware builder falls back to its legacy
        # builder when a day has only one stage. Pass the de-duplicated rows
        # here as well so a result published yesterday cannot reappear today.
        cleaned = _clean_summary_rows(cleaned, group, tournament, status)
        return old_summary_builder(day, cleaned, group, tournament, status, overrides=overrides)

    module.build_daily_summary_for_tournament = summary_builder

    def matches_menu(chat_id: int, group: str, tournament: str, day=None):
        day = day or module._active_day(chat_id)
        markup = old_menu(chat_id, group, tournament, day)
        rows = [list(row) for row in (markup or {}).get("inline_keyboard", [])]
        if any(
            str(button.get("callback_data") or "") == "day_validate"
            for row in rows for button in row
        ):
            return module._kb(rows)

        button_row = [module._btn("🔄 Проверить стадии и состав дня", "day_validate")]
        insert_at = 1 if rows and rows[0] and str(rows[0][0].get("text") or "").startswith("📅") else 0
        rows.insert(insert_at, button_row)
        return module._kb(rows)

    module._matches_menu = matches_menu

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        if data != "day_validate":
            return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

        try:
            group, tournament, day = module._current_choice(chat_id)
            if not group or not tournament:
                raise ValueError("Сначала открой турнир заново")

            module.tg_answer_callback_query(cq_id, "Проверяю игровой день по Flashscore...")
            module.tg_edit_message(
                chat_id,
                message_id,
                (
                    f"🔄 {tournament}\n"
                    f"Проверяю состав и стадии игрового дня {day:%d.%m.%Y}.\n"
                    "Парные/микст/юниоры будут исключены. Стадии сверяю по странице каждого матча."
                ),
            )

            # Refresh all three source-calendar slices through the normal session loader.
            module._load_events_for_chat(chat_id, day, force_refresh=True)
            matches = list(module._matches_for_state(chat_id, group, tournament, day))

            detected: Dict[str, list[int]] = defaultdict(list)
            previous: Dict[int, str] = {
                int(match.get("event_id") or 0): str(match.get("stage") or "")
                for match in matches
                if match.get("event_id")
            }
            max_workers = min(8, max(1, len(matches)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_stage_from_match_page, match) for match in matches]
                for future in as_completed(futures):
                    event_id, stage = future.result()
                    if event_id and stage:
                        detected[stage].append(event_id)

            found = 0
            changed = 0
            for stage, event_ids in detected.items():
                for event_id in event_ids:
                    if previous.get(event_id) != stage:
                        changed += 1
                store.save_stage(event_ids, stage)
                found += len(event_ids)

            try:
                store._CACHE.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
            module._load_events_for_chat(chat_id, day, force_refresh=True)
            final_matches = list(module._matches_for_state(chat_id, group, tournament, day))
            final_groups = store.stage_groups(module, chat_id, group, tournament, day)

            # Stage indexes may have changed after the scan, so always return to overview.
            module.set_state(
                chat_id,
                "picked_tournament",
                {"group": group, "tournament_name": tournament, "day": day.isoformat()},
            )
            module.tg_edit_message(
                chat_id,
                message_id,
                module._matches_title(chat_id, group, tournament, day),
                reply_markup=matches_menu(chat_id, group, tournament, day),
            )

            drops = _DROP_STATS.get((int(chat_id), day.isoformat()), {})
            stage_text = ", ".join(
                f"{stage}: {len(rows)}"
                for stage, rows in final_groups
            ) or "стадии не определены"
            unresolved = max(0, len(final_matches) - found)
            module.tg_send_message(
                chat_id,
                (
                    "✅ Проверка игрового дня завершена.\n"
                    f"Singles-матчей: {len(final_matches)}.\n"
                    f"Исключено парных/микста/юниоров: {int(drops.get('non_singles') or 0)}.\n"
                    f"Исключено не из этого игрового дня: {int(drops.get('wrong_day') or 0)}.\n"
                    f"Стадия подтверждена страницей матча: {found}/{len(final_matches)}; исправлено: {changed}.\n"
                    f"Сейчас: {stage_text}."
                    + (f" Не удалось считать стадию: {unresolved}." if unresolved else "")
                ),
            )
            return
        except Exception as exc:
            module.tg_answer_callback_query(cq_id, f"Ошибка проверки: {exc}", show_alert=True)
            return

    module._handle_callback = callback
    _INSTALLED = True


def install_poll(daily_summary: Any) -> None:
    """Keep doubles/mixed/juniors out of automatic daily summaries too."""
    old_target = daily_summary._is_target_event

    def target(event: Dict[str, Any], *, automatic: bool = False) -> bool:
        return is_adult_singles_event(event) and old_target(event, automatic=automatic)

    daily_summary._is_target_event = target
