from bot import dp, bot
import asyncio, os, aiohttp, asyncpg
from aiogram import Bot
from datetime import datetime as dt

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DSN       = os.getenv("DATABASE_URL")

# health-check для Vercel
async def handler(req):
    if req.method == "GET" and req.path == "/":
        return aiohttp.web.Response(text="OK")
    # webhook telegram
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler
    app = aiohttp.web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    return app

# cron-задача раз в минуту
async def check_finished():
    # TODO: проверка finished-матчей и рассылка
    pass

if __name__ != "vercel":
    asyncio.run(dp.start_polling(bot))
