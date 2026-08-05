from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict

from providers import sofascore as ss
import tournament_session_store as store


def install(module: Any) -> None:
    store.ensure_schema()
    store.install_round_capture()
    old_load = module._load_events_for_chat
    old_callback = module._handle_callback
    old_text = module._handle_text
    old_add_watch = module.add_match_watch
    old_summary_builder = module.build_daily_summary_for_tournament

    def load_events(chat_id, day=None, force_refresh=False):
        return store.session_events(old_load, chat_id, day or module._active_day(chat_id), force_refresh)

    def add_watch(chat_id, day, match):
        inserted = old_add_watch(chat_id, day, match)
        store.save_watch_metadata(chat_id, day, match)
        return inserted

    module._load_events_for_chat = load_events
    module.add_match_watch = add_watch
    module._tournaments_map = lambda chat_id, group, day=None: store.tournament_items(
        module, chat_id, group, day or module._active_day(chat_id)
    )
    module._tournaments_title = lambda chat_id, group, day: (
        f"{ss.tour_label(group)} · игровой день {day:%d.%m.%Y}\nВыбери турнир:"
    )

    def tournaments_menu(chat_id, group, day=None):
        day = day or module._active_day(chat_id)
        rows = []
        for index, item in enumerate(module._tournaments_map(chat_id, group, day)[:44], start=1):
            stages = ", ".join(f"{stage}: {count}" for stage, count in item.get("stage_counts", [])[:2])
            title = (
                f"{item.get('tournament_status', '')} · {item['tournament_name']} "
                f"({item['matches_count']} матч.; {stages})"
            )
            rows.append(
                [
                    module._btn(module._cut(title, 86), f"tour|{group}|{day.isoformat()}|{index}"),
                    module._btn("⚙️", f"tprof|{group}|{day.isoformat()}|{index}"),
                ]
            )
        rows.extend(
            [
                module._date_nav_buttons(chat_id, group, day),
                [module._btn("Ввести дату", f"session_date|schedule|{group}")],
                [module._btn("Назад", "menu|root")],
            ]
        )
        return module._kb(rows)

    module._tournaments_menu = tournaments_menu
    module._schedule_dates_menu = lambda chat_id: module._kb(
        [
            [module._btn("Сегодня", "sched_date_rel|today"), module._btn("Вчера", "sched_date_rel|yesterday")],
            [module._btn("Ввести дату", "session_date|schedule|")],
            [module._btn("Назад", "menu|root")],
        ]
    )
    module._summary_dates_menu = lambda chat_id: module._kb(
        [
            [module._btn("Сегодня", "sum_date_rel|today"), module._btn("Вчера", "sum_date_rel|yesterday")],
            [module._btn("Ввести дату", "session_date|summary|")],
            [module._btn("Назад", "menu|root")],
        ]
    )

    def current_stage_filter(chat_id, group, tournament, day):
        state, payload = module.get_state(chat_id)
        payload = payload or {}
        if (
            state == "picked_tournament"
            and payload.get("group") == group
            and payload.get("tournament_name") == tournament
            and payload.get("day") == day.isoformat()
        ):
            return str(payload.get("stage_filter") or "")
        return ""

    def matches_title(chat_id, group, tournament, day):
        groups = store.stage_groups(module, chat_id, group, tournament, day)
        stage_filter = current_stage_filter(chat_id, group, tournament, day)
        matches = [event for _stage, events in groups for event in events]
        timezone = matches[0].get("tournament_timezone", store.DEFAULT_TZ) if matches else store.DEFAULT_TZ
        cutoff = matches[0].get("tournament_cutoff_minutes", store.DEFAULT_CUTOFF) if matches else store.DEFAULT_CUTOFF
        tail = f"Стадия: {stage_filter}" if stage_filter else ("Выбери стадию:" if len(groups) > 1 else "Выбери матчи:")
        return (
            f"{ss.tour_label(group)} · {tournament}\n"
            f"Игровой день: {day:%d.%m.%Y} · {timezone} · граница {store.format_cutoff(cutoff)}\n"
            f"{tail}"
        )

    module._matches_title = matches_title

    def matches_menu(chat_id, group, tournament, day=None):
        day = day or module._active_day(chat_id)
        groups = store.stage_groups(module, chat_id, group, tournament, day)
        selected = module._selected_ids(chat_id, day)
        stage_filter = current_stage_filter(chat_id, group, tournament, day)
        rows = []
        if len(groups) > 1 and not stage_filter:
            for index, (stage, matches) in enumerate(groups, start=1):
                rows.append([module._btn(f"{stage} · {len(matches)} матч.", f"stage_view|{index}")])
        else:
            for index, (stage, matches) in enumerate(groups, start=1):
                if stage_filter and stage != stage_filter:
                    continue
                all_selected = bool(matches) and all(int(match["event_id"]) in selected for match in matches)
                rows.append(
                    [
                        module._btn(
                            ("Снять" if all_selected else "Выбрать") + f" всю стадию · {stage}",
                            f"stage_toggle|{index}",
                        )
                    ]
                )
                rows.append([module._btn("✏️ Исправить стадию", f"stage_edit|{index}")])
                for number, match in enumerate(matches[:88], start=1):
                    label = f"{number:02d}. {module._match_label(chat_id, match, int(match['event_id']) in selected)}"
                    rows.append([module._btn(module._cut(label, 100), f"watch_toggle|{match['event_id']}")])
            if len(groups) > 1:
                rows.append([module._btn("К стадиям", "stage_clear")])
        rows.extend(
            [
                [module._btn("⚙️ Настройки турнира", "tprof_current")],
                [module._btn("Готово / мои матчи", "menu|mine")],
                [module._btn("К турнирам", f"back_tours|{group}")],
            ]
        )
        return module._kb(rows)

    module._matches_menu = matches_menu

    def summary_builder(day, events, group, tournament, status="", overrides=None):
        rows = module.summary_events_for_tournament(events, group, tournament, status)
        buckets: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
        for event in rows:
            buckets[str(event.get("stage") or "Стадия не определена")].append(event)
        if len(buckets) <= 1:
            return old_summary_builder(day, events, group, tournament, status, overrides=overrides)
        blocks = []
        final_status = status
        for stage in sorted(buckets, key=lambda value: (store.STAGE_ORDER.get(value, 90), value)):
            text, stage_status, _ = old_summary_builder(
                day, buckets[stage], group, tournament, status, overrides=overrides
            )
            if text:
                if blocks:
                    text = re.sub(r"^📊 Результаты игрового дня\s*", "", text).lstrip()
                blocks.append(text)
                final_status = stage_status or final_status
        return "\n\n——————————\n\n".join(blocks), final_status, "по стадиям"

    module.build_daily_summary_for_tournament = summary_builder

    def profile_payload(chat_id):
        return dict((module.get_state(chat_id)[1] or {}))

    def profile_text(source):
        profile = store.get_profile(source)
        return (
            "⚙️ Настройки турнира\n\n"
            f"Источник: {source}\n"
            f"Название: {profile['name']}\n"
            f"Timezone: {profile['tz']}\n"
            f"Граница дня: {store.format_cutoff(profile['cutoff'])}"
        )

    def profile_menu():
        return module._kb(
            [
                [module._btn("Переименовать навсегда", "tprof_edit|name")],
                [module._btn("Изменить timezone", "tprof_edit|tz")],
                [module._btn("Изменить границу дня", "tprof_edit|cutoff")],
                [module._btn("К турнирам", "tprof_back")],
            ]
        )

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        try:
            if data.startswith("session_date|"):
                _, mode, group = data.split("|", 2)
                module.set_state(
                    chat_id,
                    "session_date_input",
                    {"mode": mode, "group": group, "editor_id": user_id},
                )
                module.tg_edit_message(chat_id, message_id, "Пришли дату: 02.08.2026")
                module.tg_answer_callback_query(cq_id)
                return

            if data.startswith("tprof|"):
                _, group, day_value, index_value = data.split("|", 3)
                day = module._parse_day(chat_id, day_value)
                item = module._tournaments_map(chat_id, group, day)[int(index_value) - 1]
                source = str(item.get("source_name") or item["tournament_name"])
                module.set_state(
                    chat_id,
                    "tournament_profile",
                    {"source": source, "group": group, "day": day.isoformat()},
                )
                module.tg_edit_message(chat_id, message_id, profile_text(source), reply_markup=profile_menu())
                module.tg_answer_callback_query(cq_id)
                return

            if data == "tprof_current":
                group, tournament, day = module._current_choice(chat_id)
                item = next(
                    item
                    for item in module._tournaments_map(chat_id, group, day)
                    if item["tournament_name"] == tournament
                )
                source = str(item.get("source_name") or tournament)
                module.set_state(
                    chat_id,
                    "tournament_profile",
                    {"source": source, "group": group, "day": day.isoformat()},
                )
                module.tg_edit_message(chat_id, message_id, profile_text(source), reply_markup=profile_menu())
                module.tg_answer_callback_query(cq_id)
                return

            if data.startswith("tprof_edit|"):
                field = data.split("|", 1)[1]
                payload = profile_payload(chat_id)
                module.set_state(chat_id, f"tprof_{field}", payload)
                prompts = {
                    "name": "Пришли правильное название.",
                    "tz": "Пришли timezone, например America/Toronto.",
                    "cutoff": "Пришли границу дня, например 06:00.",
                }
                module.tg_edit_message(chat_id, message_id, prompts[field])
                module.tg_answer_callback_query(cq_id)
                return

            if data == "tprof_back":
                payload = profile_payload(chat_id)
                day = module._parse_day(chat_id, payload.get("day"))
                group = str(payload.get("group") or "men")
                module.set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    module._tournaments_title(chat_id, group, day),
                    reply_markup=module._tournaments_menu(chat_id, group, day),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data.startswith(("stage_view|", "stage_toggle|", "stage_edit|")):
                action, index_value = data.split("|", 1)
                group, tournament, day = module._current_choice(chat_id)
                groups = store.stage_groups(module, chat_id, group, tournament, day)
                stage, matches = groups[int(index_value) - 1]
                if action == "stage_view":
                    module.set_state(
                        chat_id,
                        "picked_tournament",
                        {
                            "group": group,
                            "tournament_name": tournament,
                            "day": day.isoformat(),
                            "stage_filter": stage,
                        },
                    )
                elif action == "stage_toggle":
                    selected = module._selected_ids(chat_id, day)
                    all_selected = bool(matches) and all(int(match["event_id"]) in selected for match in matches)
                    for match in matches:
                        event_id = int(match["event_id"])
                        if all_selected:
                            module.remove_match_watch(chat_id, day, event_id)
                        elif event_id not in selected:
                            add_watch(chat_id, day, match)
                else:
                    module.set_state(
                        chat_id,
                        "stage_edit",
                        {
                            "group": group,
                            "tournament_name": tournament,
                            "day": day.isoformat(),
                            "ids": [int(match["event_id"]) for match in matches],
                            "editor_id": user_id,
                        },
                    )
                    module.tg_edit_message(
                        chat_id,
                        message_id,
                        "Пришли стадию: 1/32, 1/16, 1/8, 1/4, 1/2 или Финал",
                    )
                    module.tg_answer_callback_query(cq_id)
                    return
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    matches_title(chat_id, group, tournament, day),
                    reply_markup=matches_menu(chat_id, group, tournament, day),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data == "stage_clear":
                group, tournament, day = module._current_choice(chat_id)
                module.set_state(
                    chat_id,
                    "picked_tournament",
                    {"group": group, "tournament_name": tournament, "day": day.isoformat()},
                )
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    matches_title(chat_id, group, tournament, day),
                    reply_markup=matches_menu(chat_id, group, tournament, day),
                )
                module.tg_answer_callback_query(cq_id)
                return
        except Exception as exc:
            module.tg_answer_callback_query(cq_id, f"Ошибка: {exc}", show_alert=True)
            return
        return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

    module._handle_callback = callback

    def text_handler(chat_id, text, user_id=None):
        state, payload = module.get_state(chat_id)
        payload = payload or {}
        if payload.get("editor_id") and user_id and int(payload["editor_id"]) != int(user_id):
            return old_text(chat_id, text, user_id=user_id)
        try:
            if state == "session_date_input":
                day = store.parse_date(text)
                mode = payload.get("mode")
                group = str(payload.get("group") or "")
                if mode == "summary":
                    module.set_state(chat_id, "summary_day", {"day": day.isoformat()})
                    module.tg_send_message(
                        chat_id,
                        module._summary_groups_title(chat_id, day),
                        reply_markup=module._summary_groups_menu(chat_id, day),
                    )
                elif group:
                    module.set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
                    module.tg_send_message(
                        chat_id,
                        module._tournaments_title(chat_id, group, day),
                        reply_markup=module._tournaments_menu(chat_id, group, day),
                    )
                else:
                    module.set_state(chat_id, "schedule_day", {"day": day.isoformat()})
                    module.tg_send_message(
                        chat_id,
                        module._schedule_groups_title(chat_id, day),
                        reply_markup=module._schedule_groups_menu(day),
                    )
                return

            if state in {"tprof_name", "tprof_tz", "tprof_cutoff"}:
                source = str(payload.get("source") or "")
                if state == "tprof_name":
                    changes = {"name": text}
                elif state == "tprof_tz":
                    changes = {"tz": text.strip()}
                else:
                    changes = {"cutoff": store.parse_cutoff(text)}
                value = store.save_profile(source, **changes)
                group = str(payload.get("group") or "men")
                day = module._parse_day(chat_id, payload.get("day"))
                module.set_state(chat_id, "picked_tour_group", {"group": group, "day": day.isoformat()})
                module.tg_send_message(
                    chat_id,
                    f"Сохранено: {value['name']} · {value['tz']} · {store.format_cutoff(value['cutoff'])}",
                )
                module.tg_send_message(
                    chat_id,
                    module._tournaments_title(chat_id, group, day),
                    reply_markup=module._tournaments_menu(chat_id, group, day),
                )
                return

            if state == "stage_edit":
                stage = store.save_stage(payload.get("ids") or [], text)
                group = str(payload.get("group"))
                tournament = str(payload.get("tournament_name"))
                day = module._parse_day(chat_id, payload.get("day"))
                module.set_state(
                    chat_id,
                    "picked_tournament",
                    {
                        "group": group,
                        "tournament_name": tournament,
                        "day": day.isoformat(),
                        "stage_filter": stage,
                    },
                )
                module.tg_send_message(
                    chat_id,
                    f"Стадия сохранена для {len(payload.get('ids') or [])} матчей: {stage}",
                )
                module.tg_send_message(
                    chat_id,
                    matches_title(chat_id, group, tournament, day),
                    reply_markup=matches_menu(chat_id, group, tournament, day),
                )
                return
        except Exception as exc:
            module.tg_send_message(chat_id, f"Не получилось сохранить: {exc}")
            return
        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
