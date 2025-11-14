# ... импортируй вверху:
# from db_pg import (...), get_events_cache, set_events_cache

async def _send_tournaments_menu(chat_id: int) -> None:
    _ensure_schema_safe()
    today = _today_local(chat_id)

    # 1) читаем из кэша (его наполняет GitHub Action)
    events = get_events_cache(today)

    # 2) если кэша нет — пробуем прямой вызов (может сработать; если нет — покажем понятную подсказку)
    if not events:
        try:
            async with _client() as client:
                events = await ss.events_by_date(client, today)
            # если удалось — сразу положим в кэш, чтобы больше не дёргать источник
            if events:
                set_events_cache(today, events)
        except Exception:
            events = []

    if not events:
        await send_message(
            chat_id,
            "Расписание сегодня пока недоступно.\n"
            "Обычно кэш заполняется в течение пары минут GitHub-воркером.\n\n"
            "Можно добавить игроков вручную: `/watch Rublev, Musetti`\n"
            "или попробуйте ещё раз команду /start чуть позже.",
        )
        return

    tours = ss.group_tournaments(events)
    if not tours:
        await send_message(chat_id, "Сегодня турниров нет или расписание недоступно.")
        return

    lines = ["Выберите турнир на сегодня:"]
    keyboard = []
    for i, t in enumerate(tours, 1):
        lines.append(f"{i}) {t['name']}")
        keyboard.append([{
            "text": f"{i}) {t['name']}",
            "callback_data": f"tour:{t['id']}",
        }])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})


async def _send_matches_menu(chat_id: int, tour_id: str) -> None:
    _ensure_schema_safe()
    today = _today_local(chat_id)

    events = get_events_cache(today)
    if not events:
        try:
            async with _client() as client:
                events = await ss.events_by_date(client, today)
            if events:
                set_events_cache(today, events)
        except Exception:
            events = []

    if not events:
        await send_message(chat_id, "Список матчей пока недоступен. Попробуйте /start позже.")
        return

    tours = ss.group_tournaments(events)
    tour = next((t for t in tours if t["id"] == tour_id), None)
    if not tour:
        await send_message(chat_id, "Турнир не найден или уже недоступен.")
        return

    lines = [f"Матчи: {tour['name']}"]
    keyboard = []
    for ev in tour["events"]:
        eid = ss.event_id_of(ev)
        hn = (ev.get("homeTeam") or {}).get("name", "—")
        an = (ev.get("awayTeam") or {}).get("name", "—")
        lines.append(f"• {hn} — {an}")
        keyboard.append([{
            "text": f"Следить: {hn} — {an}",
            "callback_data": f"watch_ev:{eid}",
        }])

    keyboard.append([{
        "text": "✅ Следить за ВСЕМИ матчами турнира",
        "callback_data": f"watch_tour:{tour_id}",
    }])
    await send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard})
