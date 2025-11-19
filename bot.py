import os, asyncio, aiohttp, asyncpg, datetime as dt
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DSN       = os.getenv("DATABASE_URL")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ---------- команды ----------
@dp.message(CommandStart())
async def start(m: types.Message):
    await m.answer("👋 Привет! Используй /today – список «больших» турниров.")

@dp.message(Command("today"))
async def today(m: types.Message):
    rows = await list_tournaments()
    if not rows:
        return await m.answer("Сегодня «больших» турниров не найдено.")
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"{r['name']} ({r['country']})", callback_data=f"tour_{r['id']}")]
            for r in rows
        ]
    )
    await m.answer("Турниры сегодня:", reply_markup=kb)

@dp.callback_query(F.data.startswith("tour_"))
async def tour_matches(cq: types.CallbackQuery):
    tour_id = int(cq.data.split("_")[1])
    rows = await list_matches(tour_id)
    if not rows:
        return await cq.message.answer("Матчи ещё не добавлены.")
    kb = [
        [types.InlineKeyboardButton(text=f"{r['home']} – {r['away']}", callback_data=f"match_{r['id']}")]
        for r in rows
    ]
    await cq.message.answer("Выберите матч:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))

# ---------- парсеры ----------
async def list_tournaments():
    url = "https://api.sofascore.com/api/v1/sport/tennis/events/live"
    async with aiohttp.ClientSession() as s:
        r = await s.get(url)
        data = await r.json()
    out = []
    for ev in data.get("events", []):
        t = ev["tournament"]
        cat = t["category"]["slug"]
        if "itf" in cat or "junior" in cat:
            continue
        out.append({"id": t["uniqueId"], "name": t["name"], "country": t["category"]["name"]})
    return out

async def list_matches(tour_id):
    url = f"https://api.sofascore.com/api/v1/tournament/{tour_id}/events/last/0"
    async with aiohttp.ClientSession() as s:
        r = await s.get(url)
        data = await r.json()
    return [{"id": ev["id"], "home": ev["homeTeam"]["name"], "away": ev["awayTeam"]["name"]}
            for ev in data.get("events", []) if ev["status"]["type"] == "live"]

# ---------- запуск ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
