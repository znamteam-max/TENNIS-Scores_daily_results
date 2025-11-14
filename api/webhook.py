import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import List
from fastapi import FastAPI, Request, HTTPException
from db_pg import ensure_schema, ensure_user, set_tz, get_tz, add_watch, remove_watch, clear_today, list_today
from tg_api import send_message
from formatter import build_match_message

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
app = FastAPI()
ensure_schema()

def _today_local(chat_id: int) -> date:
    tz = ZoneInfo(get_tz(chat_id))
    return datetime.now(tz).date()

def _parse_names(text: str) -> List[str]:
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]

@app.post("/")
async def webhook(req: Request):
    if WEBHOOK_SECRET:
        token = req.headers.get("x-telegram-bot-api-secret-token")
        if token != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid secret")
    upd = await req.json()
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return {"ok": True}
    ensure_user(chat_id)

    if text.startswith("/start") or text.startswith("/help"):
        help_text = (
            "Я слежу за теннисными матчами и присылаю итоговые карточки по выбранным игрокам.\n\n"
            "Команды:\n"
            "/watch Имя1, Имя2, ... — следить СЕГОДНЯ (до 23:59)\n"
            "/add Имя — добавить игрока\n"
            "/remove Имя — убрать игрока\n"
            "/list — показать список на сегодня\n"
            "/clear — очистить список\n"
            "/tz Europe/Helsinki — поменять часовой пояс\n"
            "/format — пример карточки\n\n"
            "Фоновый воркер запускается бесплатно через GitHub Actions каждые 5 минут."
        )
        await send_message(chat_id, help_text)
        return {"ok": True}

    if text.startswith("/tz"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Укажите TZ, например: /tz Europe/Helsinki")
        else:
            import zoneinfo
            tz = toks[1].strip()
            try:
                _ = zoneinfo.ZoneInfo(tz)
                set_tz(chat_id, tz)
                await send_message(chat_id, f"Ок! Часовой пояс теперь {tz}.")
            except Exception:
                await send_message(chat_id, f"Неизвестная таймзона: {tz}")
        return {"ok": True}

    if text.startswith("/watch"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /watch De Minaur, Musetti, Rublev")
            return {"ok": True}
        names = _parse_names(toks[1])
        today = _today_local(chat_id)
        added = 0
        for n in names:
            try:
                add_watch(chat_id, n, "sofascore", today)
                added += 1
            except Exception:
                pass
        await send_message(chat_id, f"Сегодня слежу за {added} игрок(ами). Список: /list")
        return {"ok": True}

    if text.startswith("/add"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /add Sinner")
            return {"ok": True}
        name = toks[1].strip()
        add_watch(chat_id, name, "sofascore", _today_local(chat_id))
        await send_message(chat_id, f"Добавил на сегодня: {name}. /list")
        return {"ok": True}

    if text.startswith("/remove"):
        toks = text.split(maxsplit=1)
        if len(toks) < 2:
            await send_message(chat_id, "Пример: /remove Sinner")
            return {"ok": True}
        name = toks[1].strip()
        n = remove_watch(chat_id, name, _today_local(chat_id))
        await send_message(chat_id, f"Убрал: {name}" if n else "Не нашёл такого игрока в списке.")
        return {"ok": True}

    if text.startswith("/clear"):
        n = clear_today(chat_id, _today_local(chat_id))
        await send_message(chat_id, f"Ок, очистил список ({n} записей).")
        return {"ok": True}

    if text.startswith("/list"):
        rows = list_today(chat_id, _today_local(chat_id))
        if not rows:
            await send_message(chat_id, "На сегодня список пуст. Добавьте /watch или /add")
        else:
            today = _today_local(chat_id).isoformat()
            lines = [f"Сегодня ({today}):"]
            for label, resolved, _ in rows:
                lines.append(f"• {label}" + (f" (→ {resolved})" if resolved else ""))
            await send_message(chat_id, "\n".join(lines))
        return {"ok": True}

    if text.startswith("/format"):
        sample = {
            "event_id": "123",
            "home_name": "Lorenzo Musetti",
            "away_name": "Alex de Minaur",
            "score_sets": ["7:5", "3:6", "7:5"],
            "duration": "2:48",
            "home_stats": {
                "aces": 5, "doubles": 3,
                "first_serve_in_pct": 66, "first_serve_points_won_pct": 63,
                "second_serve_points_won_pct": 74,
                "winners": 22, "unforced": 28,
                "break_points_saved": 3, "break_points_faced": 5,
                "match_points_saved": 0
            },
            "away_stats": {
                "aces": 10, "doubles": 0,
                "first_serve_in_pct": 66, "first_serve_points_won_pct": 66,
                "second_serve_points_won_pct": 59,
                "winners": 34, "unforced": 44,
                "break_points_saved": 9, "break_points_faced": 12,
                "match_points_saved": 1
            }
        }
        await send_message(chat_id, build_match_message(sample))
        return {"ok": True}

    return {"ok": True}
