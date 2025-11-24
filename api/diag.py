import os, json, datetime as dt, urllib.parse

from db_pg import ensure_schema, set_events_cache, get_events_cache, ping_db

def _json(start_response, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    start_response(f"{code} OK", [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]

def _today():
    tz = os.getenv("APP_TZ", "Europe/Helsinki")
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tz)).date()
    except Exception:
        return dt.date.today()

def _sample_events(ds: dt.date):
    # МИНИМАЛЬНЫЙ МОК, похожий на Sofascore: {"events":[ ... ]}
    # Распределим по «категориям» через tournament.category.name,
    # чтобы дальше ты мог группировать: ATP / Challenger / Другие
    base_ts = int(dt.datetime(ds.year, ds.month, ds.day, 11, 0).timestamp())
    return {
        "events": [
            {
                "id": 101,
                "startTimestamp": base_ts + 3600,   # +1 час
                "homeTeam": {"name": "Jannik Sinner"},
                "awayTeam": {"name": "Alex de Minaur"},
                "tournament": {
                    "name": "ATP 500 Vienna",
                    "uniqueTournament": {"name": "ATP 500 Vienna"},
                    "category": {"id": 1, "name": "ATP"}
                }
            },
            {
                "id": 102,
                "startTimestamp": base_ts + 7200,   # +2 часа
                "homeTeam": {"name": "Stan Wawrinka"},
                "awayTeam": {"name": "Lorenzo Musetti"},
                "tournament": {
                    "name": "Challenger Helsinki",
                    "uniqueTournament": {"name": "Challenger Helsinki"},
                    "category": {"id": 2, "name": "Challenger"}
                }
            },
            {
                "id": 103,
                "startTimestamp": base_ts + 10800,  # +3 часа
                "homeTeam": {"name": "Karen Khachanov"},
                "awayTeam": {"name": "Reilly Opelka"},
                "tournament": {
                    "name": "ITF Prague M25",
                    "uniqueTournament": {"name": "ITF Prague M25"},
                    "category": {"id": 3, "name": "ITF"}
                }
            },
            {
                "id": 104,
                "startTimestamp": base_ts + 14400,  # +4 часа
                "homeTeam": {"name": "Carlos Alcaraz"},
                "awayTeam": {"name": "Frances Tiafoe"},
                "tournament": {
                    "name": "Challenger Champaign",
                    "uniqueTournament": {"name": "Challenger Champaign"},
                    "category": {"id": 2, "name": "Challenger"}
                }
            },
        ]
    }

def handler(environ, start_response):
    if environ["REQUEST_METHOD"] != "GET":
        return _json(start_response, {"ok": False, "err": "GET only"}, code=405)

    qs = urllib.parse.parse_qs(environ.get("QUERY_STRING") or "")
    path = environ.get("PATH_INFO") or "/api/diag"

    # /api/diag?seed=1[&day=YYYY-MM-DD]
    if "seed" in qs:
        ensure_schema()
        try:
            day_s = (qs.get("day") or [""])[0]
            ds = dt.date.fromisoformat(day_s) if day_s else _today()
        except Exception:
            ds = _today()
        data = _sample_events(ds)
        set_events_cache(ds, data)
        return _json(start_response, {
            "ok": True,
            "service": "diag",
            "action": "seed",
            "day": ds.isoformat(),
            "events": len(data.get("events", [])),
        })

    # /api/diag?show=1 — посмотреть что лежит в кэше на сегодня
    if "show" in qs:
        try:
            day_s = (qs.get("day") or [""])[0]
            ds = dt.date.fromisoformat(day_s) if day_s else _today()
        except Exception:
            ds = _today()
        payload = get_events_cache(ds) or {}
        return _json(start_response, {
            "ok": True,
            "service": "diag",
            "action": "show",
            "day": ds.isoformat(),
            "payload": payload
        })

    # /api/diag?db=1 — проверка подключения к БД
    if "db" in qs:
        try:
            ensure_schema()
            ok = ping_db()
            return _json(start_response, {"ok": ok, "service": "diag", "action": "db"})
        except Exception as e:
            return _json(start_response, {"ok": False, "error": str(e)}, code=500)

    # по умолчанию — краткая сводка
    info = {
        "ok": True,
        "service": "diag",
        "runtime": "wsgi",
        "python": os.sys.version,
        "path": path,
        "tips": [
            "seed=1  — залить МOК-расписание на сегодня",
            "seed=1&day=YYYY-MM-DD — залить МOК на дату",
            "show=1  — показать, что лежит в кэше",
            "db=1    — проверить БД",
        ],
    }
    return _json(start_response, info)
