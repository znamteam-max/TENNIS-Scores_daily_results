import os, asyncio, json, aiohttp, datetime as dt
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ---------- команды ----------
@dp.message(CommandStart())
async def start(m: types.Message):
    await m.answer("👋 Привет! /today – турниры без ITF.")

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

# ---------- парсер ----------
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

# ---------- веб-хук ----------
async def handler(req):
    if req.method == "POST" and req.path == "/webhook":
        update = types.Update(**await req.json())
        await dp.feed_update(bot, update)
        return aiohttp.web.Response(text="ok")
    return aiohttp.web.Response(text="use POST /webhook")

# ---------- Vercel entry ----------
from aiohttp import web
app = web.Application()
app.router.add_post("/webhook", handler)
app.router.add_get("/", lambda _: aiohttp.web.Response(text="OK"))
