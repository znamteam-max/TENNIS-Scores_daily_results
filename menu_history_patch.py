from __future__ import annotations

import datetime as dt
from typing import Any


_INSTALLED = False


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    old_root_menu = module._tour_groups_menu

    def root_menu(chat_id: int):
        """Make the difference between today's shortcuts and date navigation explicit."""
        markup = old_root_menu(chat_id)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated_row = []
            for button in row:
                item = dict(button)
                data = str(item.get("callback_data") or "")
                if data == "menu|schedule":
                    item["text"] = "📅 Другие даты"
                elif data == "group|men":
                    item["text"] = "Мужчины — сегодня"
                elif data == "group|women":
                    item["text"] = "Женщины — сегодня"
                updated_row.append(item)
            rows.append(updated_row)
        return module._kb(rows)

    def _recent_date_rows(chat_id: int, prefix: str):
        today = module._today(chat_id)
        days = [today - dt.timedelta(days=offset) for offset in range(7)]
        rows = [
            [
                module._btn("Сегодня", f"{prefix}|{days[0].isoformat()}"),
                module._btn("Вчера", f"{prefix}|{days[1].isoformat()}"),
            ]
        ]
        for start in range(2, 7, 2):
            row = [
                module._btn(day.strftime("%d.%m"), f"{prefix}|{day.isoformat()}")
                for day in days[start : start + 2]
            ]
            if row:
                rows.append(row)
        return rows

    def schedule_dates_menu(chat_id: int):
        rows = _recent_date_rows(chat_id, "sched_date")
        rows.append([module._btn("Назад", "menu|root")])
        return module._kb(rows)

    def summary_dates_menu(chat_id: int):
        rows = _recent_date_rows(chat_id, "sum_date")
        rows.append([module._btn("Назад", "menu|root")])
        return module._kb(rows)

    module._tour_groups_menu = root_menu
    module._schedule_dates_menu = schedule_dates_menu
    module._summary_dates_menu = summary_dates_menu
    _INSTALLED = True
