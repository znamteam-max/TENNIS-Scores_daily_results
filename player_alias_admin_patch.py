from __future__ import annotations

from typing import Any, Dict, List, Tuple

import db_pg


_INSTALLED = False


def _search_aliases(query: str, limit: int = 20) -> List[Tuple[str, str]]:
    q = " ".join(str(query or "").split()).strip()
    if not q:
        return []
    needle = f"%{q}%"
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            select en_full, ru
              from name_aliases
             where en_full ilike %s or ru ilike %s
             order by
               case when lower(en_full)=lower(%s) then 0
                    when lower(ru)=lower(%s) then 1
                    else 2 end,
               en_full
             limit %s
            """,
            (needle, needle, q, q, int(limit)),
        )
        return [(str(a), str(b)) for a, b in cur.fetchall()]


def _get_alias(en_full: str) -> Tuple[str, str] | None:
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("select en_full, ru from name_aliases where en_full=%s", (en_full,))
        row = cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None


def _delete_alias(en_full: str) -> bool:
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("delete from name_aliases where en_full=%s", (en_full,))
        return bool(cur.rowcount)


def _repair_known_bad_aliases() -> int:
    """Repair the confirmed Rinderknech -> Djokovic corruption without touching valid Djokovic aliases."""
    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                update name_aliases
                   set ru='Риндеркнеш'
                 where lower(en_full) like '%%rinderknech%%'
                   and lower(ru) like '%%джокович%%'
                """
            )
            return int(cur.rowcount or 0)
    except Exception as exc:
        print(f"[players] bad-alias repair failed: {exc}")
        return 0


def install(module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    repaired = _repair_known_bad_aliases()
    if repaired:
        print(f"[players] repaired Rinderknech aliases={repaired}")

    old_text = module._handle_text
    old_callback = module._handle_callback
    old_root_menu = module._tour_groups_menu

    if not any(str(row.get("command") or "") == "players" for row in module.BOT_COMMANDS):
        module.BOT_COMMANDS.append({"command": "players", "description": "база переводов фамилий игроков"})

    def _clear_alias_cache() -> None:
        try:
            module._ALIAS_CACHE.clear()
        except Exception:
            pass

    def _home_text() -> str:
        return (
            "👤 База игроков\n\n"
            "Пришли английское имя или фамилию игрока.\n"
            "Например: Rinderknech\n\n"
            "Покажу сохранённый перевод; его можно изменить или удалить.\n"
            "Отмена: /cancel"
        )

    def _search_markup(results: List[Tuple[str, str]], query: str):
        rows = []
        for idx, (en_full, ru) in enumerate(results[:20]):
            rows.append([module._btn(module._cut(f"{en_full} → {ru}", 70), f"players_open|{idx}")])
        rows.append([module._btn(f"➕ Добавить «{module._cut(query, 36)}»", "players_add")])
        rows.append([module._btn("🔎 Новый поиск", "players_home")])
        rows.append([module._btn("В начало", "menu|root")])
        return module._kb(rows)

    def _show_search(chat_id: int, query: str, *, message_id: int | None = None) -> None:
        results = _search_aliases(query)
        payload = {"query": query, "results": [[en, ru] for en, ru in results]}
        module.set_state(chat_id, "players_results", payload)
        text = f"👤 Поиск: {query}\nНайдено: {len(results)}"
        if not results:
            text += "\n\nСохранённого перевода нет. Можно добавить его кнопкой ниже."
        markup = _search_markup(results, query)
        if message_id is None:
            module.tg_send_message(chat_id, text, reply_markup=markup)
        else:
            module.tg_edit_message(chat_id, message_id, text, reply_markup=markup)

    def _record_markup():
        return module._kb(
            [
                [module._btn("✏️ Изменить русский вариант", "players_edit")],
                [module._btn("🗑 Удалить алиас", "players_delete")],
                [module._btn("← К результатам поиска", "players_back")],
                [module._btn("В начало", "menu|root")],
            ]
        )

    def root_menu(chat_id: int):
        markup = old_root_menu(chat_id)
        rows = list((markup or {}).get("inline_keyboard", []))
        if not any(any(str(btn.get("callback_data") or "") == "players_home" for btn in row) for row in rows):
            insert_at = max(0, len(rows) - 1)
            rows.insert(insert_at, [module._btn("👤 База игроков", "players_home")])
        return module._kb(rows)

    module._tour_groups_menu = root_menu

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        if data == "players_home":
            module.set_state(chat_id, "players_search", {})
            module.tg_edit_message(chat_id, message_id, _home_text())
            module.tg_answer_callback_query(cq_id)
            return

        if data.startswith("players_open|"):
            try:
                idx = int(data.split("|", 1)[1])
                _state, payload = module.get_state(chat_id)
                results = list((payload or {}).get("results") or [])
                en_full, ru = results[idx]
            except Exception:
                module.tg_answer_callback_query(cq_id, "Запись устарела. Запусти поиск заново.", show_alert=True)
                return
            module.set_state(
                chat_id,
                "players_record",
                {"en_full": str(en_full), "ru": str(ru), "query": str((payload or {}).get("query") or "")},
            )
            module.tg_edit_message(
                chat_id,
                message_id,
                f"👤 Игрок\n\nИсточник / English:\n{en_full}\n\nНаша версия:\n{ru}",
                reply_markup=_record_markup(),
            )
            module.tg_answer_callback_query(cq_id)
            return

        if data == "players_edit":
            _state, payload = module.get_state(chat_id)
            en_full = str((payload or {}).get("en_full") or "")
            ru = str((payload or {}).get("ru") or "")
            if not en_full:
                module.tg_answer_callback_query(cq_id, "Запись не выбрана", show_alert=True)
                return
            module.set_state(chat_id, "players_edit_value", dict(payload or {}))
            module.tg_edit_message(
                chat_id,
                message_id,
                f"English: {en_full}\nСейчас: {ru}\n\nПришли новый русский вариант одним сообщением.\nОтмена: /cancel",
            )
            module.tg_answer_callback_query(cq_id)
            return

        if data == "players_delete":
            _state, payload = module.get_state(chat_id)
            en_full = str((payload or {}).get("en_full") or "")
            query = str((payload or {}).get("query") or en_full)
            if not en_full:
                module.tg_answer_callback_query(cq_id, "Запись не выбрана", show_alert=True)
                return
            deleted = _delete_alias(en_full)
            _clear_alias_cache()
            module.tg_answer_callback_query(cq_id, "Удалено" if deleted else "Уже удалено")
            _show_search(chat_id, query, message_id=message_id)
            return

        if data == "players_back":
            _state, payload = module.get_state(chat_id)
            query = str((payload or {}).get("query") or "")
            if query:
                _show_search(chat_id, query, message_id=message_id)
            else:
                module.set_state(chat_id, "players_search", {})
                module.tg_edit_message(chat_id, message_id, _home_text())
            module.tg_answer_callback_query(cq_id)
            return

        if data == "players_add":
            _state, payload = module.get_state(chat_id)
            query = " ".join(str((payload or {}).get("query") or "").split()).strip()
            if not query:
                module.tg_answer_callback_query(cq_id, "Сначала введи английское имя", show_alert=True)
                return
            module.set_state(chat_id, "players_add_value", {"en_full": query, "query": query})
            module.tg_edit_message(
                chat_id,
                message_id,
                f"Добавляем:\n{query}\n\nПришли нашу русскую версию одним сообщением.\nОтмена: /cancel",
            )
            module.tg_answer_callback_query(cq_id)
            return

        return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

    module._handle_callback = callback

    def text_handler(chat_id, text, user_id=None):
        raw = " ".join(str(text or "").split()).strip()
        cmd = raw.split(" ", 1)[0].lower() if raw else ""
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        if cmd in {"/players", "players"}:
            module.set_state(chat_id, "players_search", {})
            module.tg_send_message(chat_id, _home_text())
            return

        state, payload = module.get_state(chat_id)
        payload = dict(payload or {})

        if state == "players_search":
            if not raw:
                return
            _show_search(chat_id, raw)
            return

        if state in {"players_edit_value", "players_add_value"}:
            en_full = str(payload.get("en_full") or "").strip()
            if not en_full or not raw:
                module.tg_send_message(chat_id, "Нужны и английское имя, и русский вариант. /cancel — отменить.")
                return
            db_pg.set_alias(en_full, raw)
            _clear_alias_cache()
            query = str(payload.get("query") or en_full)
            module.tg_send_message(chat_id, f"Сохранено:\n{en_full} → {raw}")
            _show_search(chat_id, query)
            return

        return old_text(chat_id, text, user_id=user_id)

    module._handle_text = text_handler
    _INSTALLED = True
