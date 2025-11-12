import asyncio, logging, os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import List
import httpx

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import TELEGRAM_BOT_TOKEN, TZ, POLL_SECONDS, DATA_SOURCE, DB_PATH, ADMIN_CHAT_ID
from storage import Storage
from formatter import build_match_message
from providers import sofascore as ss

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("tennis-bot")

storage = Storage(DB_PATH)

HELP_TEXT = (
    "Я слежу за матчами тенниса и присылаю итоговые карточки по выбранным игрокам.\n\n"
    "Команды:\n"
    "/watch Имя1, Имя2, ... — следить за этими игроками СЕГОДНЯ (до 23:59)\n"
    "/add Имя — добавить игрока в список на сегодня\n"
    "/remove Имя — убрать игрока из списка\n"
    "/list — показать текущий список на сегодня\n"
    "/clear — очистить список на сегодня\n"
    "/tz Europe/Helsinki — поменять мой часовой пояс\n"
    "/format — пример сообщения\n"
    "/help — справка и известные ограничения\n\n"
    "Как это работает: в начале дня добавьте интересующих игроков. Бот каждые ~"
    f"{POLL_SECONDS} сек проверяет матчи и, как только матч завершён, пришлёт форматированное сообщение.\n\n"
    "Ограничения:\n"
    "• Статистика (виннеры, НФ, м.б.) не всегда доступна в источнике — там будет 'н/д'.\n"
    "• Используются неофициальные JSON‑эндпоинты SofaScore; они могут меняться.\n"
    "• Русские/латинские написания имён: старайтесь писать как на английском, например 'De Minaur'.\n\n"
    "Коды ошибок: см. ниже или пришлите текст ошибки — мы поможем."
)

ERROR_GUIDE = (
    "ЧАСТЫЕ ОШИБКИ\n"
    "• E_SOFASCORE_HTTP_<code>: не удалось получить данные от SofaScore. Проверьте сеть/блокировки.\n"
    "• E_PARSE_STATS_MISSING: не удалось распарсить статистику матча (формат изменился).\n"
    "• E_NO_EVENTS_TODAY: у указанных игроков нет матчей на выбранную дату.\n"
    "• E_TG_SEND: Telegram отказал в отправке сообщения (rate limit / блок).\n"
    "• E_DB_LOCKED: БД занята — повторяем попытку.\n\n"
    "Скопируйте ошибку и пришлите её нам целиком."
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    storage.ensure_user(chat_id)
    await update.message.reply_text("Привет! "+HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT + "\n\n" + ERROR_GUIDE)

def _today_local(chat_id: int) -> date:
    tz = ZoneInfo(storage.get_tz(chat_id))
    return datetime.now(tz).date()

async def tz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Укажите таймзону, например: /tz Europe/Helsinki")
        return
    tz = " ".join(context.args).strip()
    try:
        _ = ZoneInfo(tz)
    except Exception:
        await update.message.reply_text(f"Неизвестная таймзона: {tz}")
        return
    storage.set_tz(chat_id, tz)
    await update.message.reply_text(f"Ок! Часовой пояс теперь {tz}.")

def _parse_names(text: str) -> List[str]:
    parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    storage.ensure_user(chat_id)
    if not context.args:
        await update.message.reply_text("Пример: /watch De Minaur, Musetti, Rublev")
        return
    names = _parse_names(" ".join(context.args))
    today = _today_local(chat_id).isoformat()
    added = 0
    for n in names:
        try:
            storage.add_watch(chat_id, n, provider='sofascore', expires_on=today)
            added += 1
        except Exception as e:
            log.error("DB err add_watch: %s", e)
    await update.message.reply_text(f"Сегодня слежу за {added} игрок(ами). Список: /list")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Пример: /add Sinner")
        return
    name = " ".join(context.args).strip()
    today = _today_local(chat_id).isoformat()
    storage.add_watch(chat_id, name, 'sofascore', today)
    await update.message.reply_text(f"Добавил на сегодня: {name}. /list")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Пример: /remove Sinner")
        return
    name = " ".join(context.args).strip()
    today = _today_local(chat_id).isoformat()
    n = storage.remove_watch(chat_id, name, today)
    if n:
        await update.message.reply_text(f"Убрал: {name}")
    else:
        await update.message.reply_text("Ничего не убрал (не нашёл в сегодняшнем списке).")

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    today = _today_local(chat_id).isoformat()
    n = storage.clear_today(chat_id, today)
    await update.message.reply_text(f"Ок, очистил список ({n} записей).")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    today = _today_local(chat_id).isoformat()
    rows = storage.list_today(chat_id, today)
    if not rows:
        await update.message.reply_text("На сегодня список пуст. Добавьте /watch или /add")
        return
    lines = [f"Сегодня ({today}):"]
    for (label, resolved, _) in rows:
        if resolved:
            lines.append(f"• {label} (→ {resolved})")
        else:
            lines.append(f"• {label}")
    await update.message.reply_text("\n".join(lines))

async def format_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(build_match_message(sample))

async def _worker(app: Application):
    log.info("Worker loop started, source=%s", DATA_SOURCE)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                # For each chat: check their today watchlist
                # Naive approach: pull all chats from users table (small scale).
                # Or maintain in-memory set of active chats.
                # We'll scan today watchlists and query matching events; then push finished & not-notified.
                # For simplicity, fetch all chat_ids from users.
                import sqlite3
                con = sqlite3.connect(DB_PATH)
                rows = con.execute("SELECT chat_id FROM users").fetchall()
                con.close()
                for (chat_id,) in rows:
                    today = _today_local(chat_id).isoformat()
                    watch = storage.list_today(chat_id, today)
                    if not watch:
                        continue
                    player_names = [w[0] for w in watch]
                    try:
                        events = await ss.find_player_events_today(client, _today_local(chat_id), player_names)
                    except httpx.HTTPError as e:
                        log.warning("E_SOFASCORE_HTTP_%s: %s", getattr(e.response,'status_code', 'X'), str(e))
                        continue
                    if not events:
                        # ignore E_NO_EVENTS_TODAY spam; only informative on /today command (not implemented)
                        pass
                    for ev in events:
                        if not ss.is_finished(ev):
                            continue
                        eid = ss.event_id_of(ev)
                        if not eid:
                            continue
                        if storage.was_notified(chat_id, 'sofascore', eid, today):
                            continue
                        try:
                            data = await ss.event_statistics(client, int(eid))
                            msg = build_match_message(data)
                            await app.bot.send_message(chat_id=chat_id, text=msg)
                            storage.mark_notified(chat_id, 'sofascore', eid, today)
                        except httpx.HTTPError as e:
                            log.error("E_SOFASCORE_HTTP_%s: %s", getattr(e.response,'status_code', 'X'), str(e))
                        except Exception as e:
                            log.exception("E_PARSE_STATS_MISSING: %s", e)
                await asyncio.sleep(POLL_SECONDS)
            except Exception as e:
                log.exception("Worker loop error: %s", e)
                await asyncio.sleep(5)

async def main():
    token = TELEGRAM_BOT_TOKEN
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("tz", tz_cmd))
    application.add_handler(CommandHandler("watch", watch_cmd))
    application.add_handler(CommandHandler("add", add_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(CommandHandler("clear", clear_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("format", format_cmd))

    # Start background worker
    application.job_queue.run_once(lambda *_: asyncio.create_task(_worker(application)), when=1)

    log.info("Starting long polling...")
    await application.run_polling(close_loop=False)

if __name__ == "__main__":
    asyncio.run(main())
