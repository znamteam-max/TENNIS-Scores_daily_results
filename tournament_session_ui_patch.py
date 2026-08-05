from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

import tournament_session_store as store


STAGE_CHOICES = {
    "q": "Квалификация",
    "64": "1/64 финала",
    "32": "1/32 финала",
    "16": "1/16 финала",
    "8": "1/8 финала",
    "4": "1/4 финала",
    "2": "1/2 финала",
    "f": "Финал",
}
UNKNOWN_STAGE = "Стадия не определена"
UNKNOWN_STAGE_LABEL = "Раунд не найден"


def install(module: Any) -> None:
    """Polish tournament profile and stage controls without changing the poll gate."""
    old_tournaments_menu = module._tournaments_menu
    old_matches_menu = module._matches_menu
    old_callback = module._handle_callback
    old_text = module._handle_text

    # A number of matches played during one session does not reliably identify a round.
    # Use only provider metadata or a saved manual/automatic override.
    def exact_apply_stages(events):
        overrides = store.stage_overrides(int(event.get("event_id") or 0) for event in events)
        for event in events:
            stage = store.event_stage(event, overrides) or UNKNOWN_STAGE
            event["stage"] = stage
            event.setdefault("raw", {})["flashscore_round"] = stage

    store.apply_stages = exact_apply_stages
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass

    def display_stage_text(value: Any) -> str:
        return str(value or "").replace(UNKNOWN_STAGE, UNKNOWN_STAGE_LABEL)

    def tournaments_menu(chat_id, group, day=None):
        markup = old_tournaments_menu(chat_id, group, day)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated = [dict(button) for button in row]
            for button in updated:
                button["text"] = display_stage_text(button.get("text"))
            if (
                len(updated) == 2
                and str((updated[1] or {}).get("callback_data") or "").startswith("tprof|")
            ):
                rows.append([updated[0]])
            else:
                rows.append(updated)
        return module._kb(rows)

    module._tournaments_menu = tournaments_menu

    def matches_menu(chat_id, group, tournament, day=None):
        markup = old_matches_menu(chat_id, group, tournament, day)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated = []
            unknown_row = False
            detect_index = ""
            for button in row:
                button = dict(button)
                original_text = str(button.get("text") or "")
                callback_data = str(button.get("callback_data") or "")
                if button.get("callback_data") == "tprof_current":
                    button["text"] = module._cut(f"⚙️ Настройки: {tournament}", 48)
                elif callback_data.startswith("stage_edit|"):
                    button["text"] = "✏️ Указать раунд вручную"
                else:
                    button["text"] = display_stage_text(original_text)
                if UNKNOWN_STAGE in original_text and callback_data.startswith(("stage_view|", "stage_toggle|")):
                    unknown_row = True
                    detect_index = callback_data.split("|", 1)[1]
                updated.append(button)
            rows.append(updated)
            if unknown_row and detect_index:
                rows.append([module._btn("🔎 Определить раунд автоматически", f"stage_detect|{detect_index}")])
        return module._kb(rows)

    module._matches_menu = matches_menu

    def profile_payload(chat_id: int) -> Dict[str, Any]:
        return dict((module.get_state(chat_id)[1] or {}))

    def profile_text(source: str) -> str:
        profile = store.get_profile(source)
        return (
            f"⚙️ Настройки турнира «{profile['name']}»\n\n"
            f"Текущее название: {profile['name']}\n"
            f"Название источника: {source}\n"
            f"Timezone: {profile['tz']}\n"
            f"Граница игрового дня: {store.format_cutoff(profile['cutoff'])}"
        )

    def profile_menu(source: str):
        profile = store.get_profile(source)
        current = module._cut(str(profile["name"]), 36)
        return module._kb(
            [
                [module._btn(f"✏️ Изменить название: {current}", "tprof_edit|name")],
                [module._btn("Изменить timezone", "tprof_edit|tz")],
                [module._btn("Изменить границу дня", "tprof_edit|cutoff")],
                [module._btn("К турниру", "tprof_back")],
            ]
        )

    def stage_choice_menu(index: int):
        return module._kb(
            [
                [
                    module._btn("1/64", f"stage_set|{index}|64"),
                    module._btn("1/32", f"stage_set|{index}|32"),
                ],
                [
                    module._btn("1/16", f"stage_set|{index}|16"),
                    module._btn("1/8", f"stage_set|{index}|8"),
                ],
                [
                    module._btn("1/4", f"stage_set|{index}|4"),
                    module._btn("1/2", f"stage_set|{index}|2"),
                ],
                [
                    module._btn("Квалификация", f"stage_set|{index}|q"),
                    module._btn("Финал", f"stage_set|{index}|f"),
                ],
                [module._btn("Назад к матчам", "stage_back")],
            ]
        )

    def open_profile(chat_id, message_id, cq_id, group, day, tournament, source):
        module.set_state(
            chat_id,
            "tournament_profile",
            {
                "source": source,
                "group": group,
                "day": day.isoformat(),
                "tournament_name": tournament,
            },
        )
        module.tg_edit_message(
            chat_id,
            message_id,
            profile_text(source),
            reply_markup=profile_menu(source),
        )
        module.tg_answer_callback_query(cq_id)

    def current_stage_group(chat_id: int, index_value: str):
        group, tournament, day = module._current_choice(chat_id)
        groups = store.stage_groups(module, chat_id, group, tournament, day)
        index = int(index_value) - 1
        if index < 0 or index >= len(groups):
            raise ValueError("Группа матчей уже изменилась. Открой турнир заново.")
        stage, matches = groups[index]
        return group, tournament, day, index + 1, stage, matches

    def refresh_tournament(chat_id: int, message_id: int, group: str, tournament: str, day, stage_filter: str = ""):
        payload = {
            "group": group,
            "tournament_name": tournament,
            "day": day.isoformat(),
        }
        if stage_filter:
            payload["stage_filter"] = stage_filter
        module.set_state(chat_id, "picked_tournament", payload)
        module.tg_edit_message(
            chat_id,
            message_id,
            module._matches_title(chat_id, group, tournament, day),
            reply_markup=module._matches_menu(chat_id, group, tournament, day),
        )

    def detect_from_match_page(match: Dict[str, Any]) -> tuple[int, str]:
        try:
            import match_card

            stage = match_card._stage_from_flashscore_page(match)  # type: ignore[attr-defined]
            return int(match["event_id"]), store.normalize_stage(stage)
        except Exception:
            return int(match.get("event_id") or 0), ""

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        try:
            if data.startswith("tprof|"):
                _, group, day_value, index_value = data.split("|", 3)
                day = module._parse_day(chat_id, day_value)
                item = module._tournaments_map(chat_id, group, day)[int(index_value) - 1]
                tournament = str(item["tournament_name"])
                source = str(item.get("source_name") or tournament)
                open_profile(chat_id, message_id, cq_id, group, day, tournament, source)
                return

            if data == "tprof_current":
                group, tournament, day = module._current_choice(chat_id)
                item = next(
                    item
                    for item in module._tournaments_map(chat_id, group, day)
                    if item["tournament_name"] == tournament
                )
                source = str(item.get("source_name") or tournament)
                open_profile(chat_id, message_id, cq_id, group, day, tournament, source)
                return

            if data == "tprof_edit|name":
                payload = profile_payload(chat_id)
                source = str(payload.get("source") or "")
                current = store.get_profile(source)["name"]
                if user_id:
                    payload["editor_id"] = int(user_id)
                module.set_state(chat_id, "tprof_name", payload)
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    (
                        f"Текущее название: {current}\n\n"
                        "Пришли новое название турнира одним сообщением. "
                        "Оно сохранится для следующих игровых дней."
                    ),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data == "tprof_back":
                payload = profile_payload(chat_id)
                tournament = str(payload.get("tournament_name") or "")
                if tournament:
                    day = module._parse_day(chat_id, payload.get("day"))
                    group = str(payload.get("group") or "men")
                    current_name = str(store.get_profile(str(payload.get("source") or tournament))["name"])
                    refresh_tournament(chat_id, message_id, group, current_name, day)
                    module.tg_answer_callback_query(cq_id)
                    return

            if data.startswith("stage_edit|"):
                _, index_value = data.split("|", 1)
                group, tournament, day, index, stage, matches = current_stage_group(chat_id, index_value)
                module.tg_edit_message(
                    chat_id,
                    message_id,
                    (
                        f"Турнир: {tournament}\n"
                        f"Сейчас: {display_stage_text(stage)}\n"
                        f"Матчей в группе: {len(matches)}\n\n"
                        "Выбери правильный раунд:"
                    ),
                    reply_markup=stage_choice_menu(index),
                )
                module.tg_answer_callback_query(cq_id)
                return

            if data.startswith("stage_set|"):
                _, index_value, token = data.split("|", 2)
                if token not in STAGE_CHOICES:
                    raise ValueError("Неизвестный раунд")
                group, tournament, day, _index, _old_stage, matches = current_stage_group(chat_id, index_value)
                stage = store.save_stage((int(match["event_id"]) for match in matches), STAGE_CHOICES[token])
                refresh_tournament(chat_id, message_id, group, tournament, day, stage_filter=stage)
                module.tg_answer_callback_query(cq_id, f"Сохранено: {stage}")
                return

            if data.startswith("stage_detect|"):
                _, index_value = data.split("|", 1)
                group, tournament, day, _index, _stage, matches = current_stage_group(chat_id, index_value)
                module.tg_answer_callback_query(cq_id, "Проверяю страницы матчей...")
                targets = list(matches[:4])
                detected: Dict[str, list[int]] = defaultdict(list)
                with ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
                    futures = [executor.submit(detect_from_match_page, match) for match in targets]
                    for future in as_completed(futures):
                        event_id, stage = future.result()
                        if event_id and stage:
                            detected[stage].append(event_id)
                found = sum(len(ids) for ids in detected.values())
                for stage, event_ids in detected.items():
                    store.save_stage(event_ids, stage)
                refresh_tournament(chat_id, message_id, group, tournament, day)
                if found:
                    details = ", ".join(f"{stage}: {len(ids)}" for stage, ids in detected.items())
                    remaining = max(0, len(matches) - found)
                    tail = f" Осталось проверить: {remaining}." if remaining else ""
                    module.tg_send_message(chat_id, f"Раунд определён: {details}.{tail}")
                else:
                    module.tg_send_message(
                        chat_id,
                        "Flashscore не отдал раунд и на страницах этих матчей. Нажми «Указать раунд вручную».",
                    )
                return

            if data == "stage_back":
                group, tournament, day = module._current_choice(chat_id)
                refresh_tournament(chat_id, message_id, group, tournament, day)
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
        if state == "tprof_name":
            editor_id = payload.get("editor_id")
            if editor_id and user_id and int(editor_id) != int(user_id):
                return old_text(chat_id, text, user_id=user_id)
            try:
                source = str(payload.get("source") or "")
                value = store.save_profile(source, name=text)
                payload["tournament_name"] = value["name"]
                payload.pop("editor_id", None)
                module.set_state(chat_id, "tournament_profile", payload)
                module.tg_send_message(chat_id, f"Название сохранено: {value['name']}")
                module.tg_send_message(
                    chat_id,
                    profile_text(source),
                    reply_markup=profile_menu(source),
                )
            except Exception as exc:
                module.tg_send_message(chat_id, f"Не получилось сохранить название: {exc}")
            return
        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
