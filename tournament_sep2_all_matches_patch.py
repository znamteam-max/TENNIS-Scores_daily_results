from __future__ import annotations

from typing import Any


def install(module: Any) -> None:
    old_menu = module._matches_menu
    old_callback = module._handle_callback

    def _choice(chat_id):
        group, tournament, day = module._current_choice(chat_id)
        if not group or not tournament:
            raise ValueError("Сначала открой турнир заново")
        return group, tournament, day

    def _is_all_mode(chat_id: int, group: str, tournament: str, day) -> bool:
        state, payload = module.get_state(chat_id)
        payload = payload or {}
        return (
            state == "picked_tournament"
            and payload.get("group") == group
            and payload.get("tournament_name") == tournament
            and payload.get("day") == day.isoformat()
            and payload.get("stage_filter") == "__all__"
        )

    def matches_menu(chat_id: int, group: str, tournament: str, day=None):
        day = day or module._active_day(chat_id)
        if not _is_all_mode(chat_id, group, tournament, day):
            markup = old_menu(chat_id, group, tournament, day)
            rows = []
            for row in (markup or {}).get("inline_keyboard", []):
                updated = []
                for button in row:
                    item = dict(button)
                    if str(item.get("text") or "").startswith("📅 МАТЧИ СЕГОДНЯ"):
                        item["callback_data"] = "day_all"
                    updated.append(item)
                rows.append(updated)
            return module._kb(rows)

        matches = module._matches_for_state(chat_id, group, tournament, day)
        selected = module._selected_ids(chat_id, day)
        rows = [[module._btn(f"📅 ВСЕ МАТЧИ · {len(matches)}", "noop")]]
        for number, match in enumerate(matches[:88], start=1):
            label = f"{number:02d}. {module._match_label(chat_id, match, int(match['event_id']) in selected)}"
            rows.append([module._btn(module._cut(label, 100), f"watch_toggle|{match['event_id']}")])
        rows.extend(
            [
                [module._btn("К стадиям", "stage_clear")],
                [module._btn("⚙️ Настройки турнира", "tprof_current")],
                [module._btn("Готово / мои матчи", "menu|mine")],
                [module._btn("К турнирам", f"back_tours|{group}")],
            ]
        )
        return module._kb(rows)

    module._matches_menu = matches_menu

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        if data != "day_all":
            return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)
        try:
            group, tournament, day = _choice(chat_id)
            module.set_state(
                chat_id,
                "picked_tournament",
                {
                    "group": group,
                    "tournament_name": tournament,
                    "day": day.isoformat(),
                    "stage_filter": "__all__",
                },
            )
            matches = module._matches_for_state(chat_id, group, tournament, day)
            title = module._matches_title(chat_id, group, tournament, day)
            title = title.replace("Стадия: __all__", f"Все матчи игрового дня: {len(matches)}")
            module.tg_edit_message(
                chat_id,
                message_id,
                title,
                reply_markup=matches_menu(chat_id, group, tournament, day),
            )
            module.tg_answer_callback_query(cq_id, f"Показываю все матчи: {len(matches)}")
        except Exception as exc:
            module.tg_answer_callback_query(cq_id, f"Ошибка: {exc}", show_alert=True)

    module._handle_callback = callback
