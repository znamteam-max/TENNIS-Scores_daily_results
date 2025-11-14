import os
import json
import random
import asyncio
import datetime as dt
import httpx

# Два эндпоинта Sofascore — пробуем оба по очереди
BASES = [
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]

# Нормальный браузерный набор заголовков
HEADERS = {
    "User-Agent": os.getenv(
        "SOFA_UA",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache",
}

class SofascoreChallenge(Exception):
    """Сигнализируем вызывающему коду о временной защите/не-JSON ответе."""
    pass

def _ds(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")

def _json_from_text(t: str):
    s = t.lstrip()
    # иногда Sofascore присылает префикс вида while(1);
    if s.startswith("while(1);"):
        s = s[len("while(1);"):]
    return json.loads(s)

async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    r = await client.get(url, headers=HEADERS, timeout=20.0)
    # Явный 403 — бросаем читаемое исключение
    if r.status_code == 403:
        raise SofascoreChallenge(f"403 challenge at {url}")
    ct = (r.headers.get("content-type") or "").lower()
    text = r.text or ""
    # Если не JSON — пробуем распарсить, иначе считаем это челленджем/HTML
    if "json" not in ct:
        try:
            return _json_from_text(text)
        except Exception:
            raise SofascoreChallenge(f"non-json response {r.status_code} at {url}")
    try:
        return r.json()
    except Exception:
        # На всякий — вторая попытка через ручной парсинг
        try:
            return _json_from_text(text)
        except Exception:
            raise SofascoreChallenge(f"invalid json at {url}")

async def _try_get(client: httpx.AsyncClient, path: str) -> dict:
    last_exc: Exception | None = None
    for base in BASES:
        url = f"{base}{path}"
        try:
            # небольшой джиттер между попытками
            await asyncio.sleep(0.25 + random.random() * 0.35)
            return await _get_json(client, url)
        except SofascoreChallenge as e:
            last_exc = e
        except Exception as e:
            last_exc = e
    if last_exc:
        raise last_exc
    return {}

# ----------------- Публичные функции-провайдеры -----------------

async def events_by_date(client: httpx.AsyncClient, d: dt.date) -> list[dict]:
    """
    Возвращает список событий (даже если Sofascore кинул challenge, мы не падаем).
    """
    try:
        data = await _try_get(client, f"/sport/tennis/scheduled-events/{_ds(d)}")
        return data.get("events", []) or []
    except SofascoreChallenge as e:
        # Позволяем вызывающему коду решить: молча вернуться с пустым списком или залогировать
        raise e

async def live_events(client: httpx.AsyncClient) -> list[dict]:
    try:
        data = await _try_get(client, "/sport/tennis/events/live")
        return data.get("events", []) or []
    except SofascoreChallenge as e:
        raise e
