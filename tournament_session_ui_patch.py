from __future__ import annotations

from typing import Any, Dict

import tournament_session_store as store


def install(module: Any) -> None:
    """Polish the tournament profile UI without changing session logic."""
    old_tournaments_menu = module._tournaments_menu
    old_matches_menu = module._matches_menu
    old_callback = module._handle_callback
    old_text = module._handle_text

    def tournaments_menu(chat_id, group, day=None):
        markup = old_tournaments_menu(chat_id, group, day)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            if (
                len(row) == 2
                and str((row[1] or {}).get("callback_data") or "").startswith("tprof|")
            ):
                rows.append([row[0]])
            else:
                rows.append(row)
        return module._kb(rows)

    module._tournaments_menu = tournaments_menu

    def matches_menu(chat_id, group, tournament, day=None):
        markup = old_matches_menu(chat_id, group, tournament, day)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated = []
            for button in row:
                button = dict(button)
                if button.get("callback_data") == "tprof_current":
                    button["text"] = module._cut(f"⚙️ Настройки: {tournament}", 48)
                updated.append(button)
            rows.append(updated)
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
                    module.set_state(
                        chat_id,
                        "picked_tournament",
                        {
                            "group": group,
                            "tournament_name": current_name,
                            "day": day.isoformat(),
                        },
                    )
                    module.tg_edit_message(
                        chat_id,
                        message_id,
                        module._matches_title(chat_id, group, current_name, day),
                        reply_markup=module._matches_menu(chat_id, group, current_name, day),
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
