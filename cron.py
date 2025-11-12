
import os, asyncio, httpx
from datetime import datetime, date
from zoneinfo import ZoneInfo
from fastapi import FastAPI
from db_pg import ensure_schema, all_chat_ids, list_today, was_notified, mark_notified, get_tz
from providers import sofascore as ss
from formatter import build_match_message
from tg_api import send_message

app = FastAPI()
ensure_schema()

def _today_local_for(chat_id: int) -> date:
    tz = ZoneInfo(get_tz(chat_id))
    return datetime.now(tz).date()

@app.get("/")
async def run():
    async with httpx.AsyncClient() as client:
        for chat_id in all_chat_ids():
            today = _today_local_for(chat_id)
            watch = list_today(chat_id, today)
            if not watch:
                continue
            player_names = [w[0] for w in watch]
            try:
                events = await ss.find_player_events_today(client, today, player_names)
            except httpx.HTTPError as e:
                # log but continue
                continue
            for ev in events:
                if not ss.is_finished(ev):
                    continue
                eid = ss.event_id_of(ev)
                if not eid:
                    continue
                if was_notified(chat_id, 'sofascore', eid, today):
                    continue
                try:
                    data = await ss.event_statistics(client, int(eid))
                    msg = build_match_message(data)
                    await send_message(chat_id, msg)
                    mark_notified(chat_id, 'sofascore', eid, today)
                except Exception:
                    # swallow, continue other chats
                    pass
    return {"ok": True}
