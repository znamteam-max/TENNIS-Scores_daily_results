import os, json, datetime as dt

# Пытаемся импортировать psycopg v3; если нет — fallback на psycopg2 под тем же именем
try:
    import psycopg  # v3
except ModuleNotFoundError:  # fallback
    import psycopg2 as psycopg  # type: ignore

def _url() -> str:
    url = os.getenv("POSTGRES_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL is not set")
    return url

def _conn():
    # и для v3, и для psycopg2 имя функции одинаковое
    return psycopg.connect(_url())

def ensure_schema() -> None:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("""
            create table if not exists schedule_cache(
                d date primary key,
                payload jsonb not null,
                updated_at timestamptz not null default now()
            );
            """)
        con.commit()

def cache_schedule(d: dt.date, events: list[dict]) -> None:
    # храним как json-строку чтобы не упираться в адаптеры
    payload = json.dumps(events, ensure_ascii=False)
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("""
                insert into schedule_cache(d, payload, updated_at)
                values (%s, %s, now())
                on conflict (d) do update
                set payload = excluded.payload,
                    updated_at = now();
            """, (d, payload))
        con.commit()

def read_schedule(d: dt.date) -> list[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("select payload from schedule_cache where d = %s", (d,))
            row = cur.fetchone()
    if not row or not row[0]:
        return []
    # row[0] — строка JSON
    try:
        return json.loads(row[0])
    except Exception:
        return []
