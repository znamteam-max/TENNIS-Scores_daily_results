from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import db_pg


_INSTALLED = False


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _search_aliases(query: str, limit: int = 40) -> List[Tuple[str, str]]:
    q = _norm(query)
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


def _alias_map_for(names: List[str]) -> Dict[str, str]:
    names = list(dict.fromkeys(_norm(x) for x in names if _norm(x)))
    if not names:
        return {}
    with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute("select en_full, ru from name_aliases where en_full=any(%s)", (names,))
        return {str(a): str(b) for a, b in cur.fetchall()}


def _extract_event_names(data: Any) -> List[str]:
    out: List[str] = []
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return out
    if not isinstance(data, dict):
        return out
    for event in data.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        for side in ("homeCompetitor", "awayCompetitor", "homePlayer", "awayPlayer", "homeTeam", "awayTeam", "home", "away"):
            obj = event.get(side)
            if not isinstance(obj, dict):
                continue
            for key in ("name", "shortName"):
                name = _norm(obj.get(key))
                if name and name.upper() != "TBD":
                    out.append(name)
    return out


def _search_known_players(query: str, limit: int = 30) -> List[Tuple[str, str]]:
    """Search actual player/source names known from recent schedules/history, then attach alias if any."""
    q = _norm(query)
    if not q:
        return []
    needle = f"%{q}%"
    names: List[str] = []

    # 1) Current/recent schedule cache: this is the closest thing to our player directory.
    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                select data
                  from events_cache
                 where data is not null
                   and data::text ilike %s
                 order by ds desc
                 limit 21
                """,
                (needle,),
            )
            for (data,) in cur.fetchall():
                names.extend(_extract_event_names(data))
    except Exception as exc:
        print(f"[players] events_cache search failed: {exc}")

    # 2) Match history keeps names even after an event leaves the current schedule.
    try:
        with db_pg._conn() as con, con.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                select home_name from match_watches where home_name ilike %s
                union
                select away_name from match_watches where away_name ilike %s
                limit 60
                """,
                (needle, needle),
            )
            names.extend(str(row[0]) for row in cur.fetchall() if row and row[0])
    except Exception as exc:
        print(f"[players] match_watches search failed: {exc}")

    # 3) Existing alias source names are also valid known player/source names.
    aliases = _search_aliases(q, limit=60)
    names.extend(en for en, _ru in aliases)

    q_low = q.lower()
    unique = []
    seen = set()
    for name in names:
        name = _norm(name)
        if not name or q_low not in name.lower() or name.lower() in seen:
            continue
        seen.add(name.lower())
        unique.append(name)

    unique.sort(key=lambda name: (0 if name.lower() == q_low else 1 if name.lower().startswith(q_low) else 2, len(name), name.lower()))
    unique = unique[:limit]
    alias_map = _alias_map_for(unique)
    return [(name, alias_map.get(name, "")) for name in unique]


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
        module.BOT_COMMANDS.append({"command": "players", "description": "найти игрока и исправить его имя"})

    def _clear_alias_cache() -> None:
        try:
            module._ALIAS_CACHE.clear()
        except Exception:
            pass

    def _home_text() -> str:
        return (
            "👤 База игроков\n\n"
            "Пришли фамилию или имя игрока как оно приходит из источника.\n"
            "Например: Fritz или Rinderknech.\n\n"
            "Сначала найду самого игрока в расписаниях/истории, затем покажу нашу сохранённую версию, если она есть.\n"
            "Отмена: /cancel"
        )

    def _search_markup(results: List[Tuple[str, str]]):
        rows = []
        for idx, (source_name, alias) in enumerate(results[:30]):
            tail = alias if alias else "без нашей версии"
            rows.append([module._btn(module._cut(f"{source_name} → {tail}", 76), f"players_open|{idx}")])
        rows.append([module._btn("🔎 Новый поиск", "players_home")])
        rows.append([module._btn("В начало", "menu|root")])
        return module._kb(rows)

    def _show_search(chat_id: int, query: str, *, message_id: int | None = None) -> None:
        results = _search_known_players(query)
        payload = {"query": query, "results": [[name, alias] for name, alias in results]}
        module.set_state(chat_id, "players_results", payload)
        text = f"👤 Поиск игрока: {query}\nНайдено игроков: {len(results)}"
        if not results:
            text += (
                "\n\nИгрока с таким исходным именем пока нет в наших недавних расписаниях/истории. "
                "Попробуй часть фамилии или точное написание из Flashscore."
            )
        markup = _search_markup(results)
        if message_id is None:
            module.tg_send_message(chat_id, text, reply_markup=markup)
        else:
            module.tg_edit_message(chat_id, message_id, text, reply_markup=markup)

    def _record_markup(has_alias: bool):
        rows = [[module._btn("✏️ Изменить / добавить нашу версию", "players_edit")]]
        if has_alias:
            rows.append([module._btn("🗑 Удалить нашу версию", "players_delete")])
        rows.extend([
            [module._btn("← К результатам поиска", "players_back")],
            [module._btn("В начало", "menu|root")],
        ])
        return module._kb(rows)

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
                source_name, alias = results[idx]
            except Exception:
                module.tg_answer_callback_query(cq_id, "Запись устарела. Запусти поиск заново.", show_alert=True)
                return
            source_name, alias = str(source_name), str(alias or "")
            module.set_state(
                chat_id,
                "players_record",
                {"en_full": source_name, "ru": alias, "query": str((payload or {}).get("query") or "")},
            )
            module.tg_edit_message(
                chat_id,
                message_id,
                (
                    f"👤 Игрок найден\n\n"
                    f"Имя из источника:\n{source_name}\n\n"
                    f"Наша версия:\n{alias or 'не сохранена'}"
                ),
                reply_markup=_record_markup(bool(alias)),
            )
            module.tg_answer_callback_query(cq_id)
            return

        if data == "players_edit":
            _state, payload = module.get_state(chat_id)
            en_full = str((payload or {}).get("en_full") or "")
            ru = str((payload or {}).get("ru") or "")
            if not en_full:
                module.tg_answer_callback_query(cq_id, "Игрок не выбран", show_alert=True)
                return
            module.set_state(chat_id, "players_edit_value", dict(payload or {}))
            module.tg_edit_message(
                chat_id,
                message_id,
                f"Имя из источника: {en_full}\nСейчас: {ru or 'не сохранено'}\n\nПришли нашу версию одним сообщением.\nОтмена: /cancel",
            )
            module.tg_answer_callback_query(cq_id)
            return

        if data == "players_delete":
            _state, payload = module.get_state(chat_id)
            en_full = str((payload or {}).get("en_full") or "")
            query = str((payload or {}).get("query") or en_full)
            if not en_full:
                module.tg_answer_callback_query(cq_id, "Игрок не выбран", show_alert=True)
                return
            deleted = _delete_alias(en_full)
            _clear_alias_cache()
            module.tg_answer_callback_query(cq_id, "Наша версия удалена" if deleted else "Она уже отсутствует")
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

        return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

    module._handle_callback = callback

    def text_handler(chat_id, text, user_id=None):
        raw = _norm(text)
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
            if raw:
                _show_search(chat_id, raw)
            return

        if state == "players_edit_value":
            en_full = str(payload.get("en_full") or "").strip()
            if not en_full or not raw:
                module.tg_send_message(chat_id, "Нужны имя игрока из источника и наша версия. /cancel — отменить.")
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
