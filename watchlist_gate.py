from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional


DEFAULT_KEY = "tennis-results-active-watchlist"


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def configured() -> bool:
    return bool(_env("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")) and bool(
        _env("WATCHLIST_KV_NAMESPACE_ID", "CLOUDFLARE_KV_NAMESPACE_ID", "CF_KV_NAMESPACE_ID")
    ) and bool(_env("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"))


def _kv_url() -> str:
    account_id = urllib.parse.quote(_env("CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID"))
    namespace_id = urllib.parse.quote(
        _env("WATCHLIST_KV_NAMESPACE_ID", "CLOUDFLARE_KV_NAMESPACE_ID", "CF_KV_NAMESPACE_ID")
    )
    key = urllib.parse.quote(os.getenv("WATCHLIST_KV_KEY", DEFAULT_KEY).strip() or DEFAULT_KEY, safe="")
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{namespace_id}/values/{key}"


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_env('CLOUDFLARE_API_TOKEN', 'CF_API_TOKEN')}",
        "Content-Type": "application/json; charset=utf-8",
    }


def load() -> Optional[Dict[str, Any]]:
    if not configured():
        return None
    req = urllib.request.Request(_kv_url(), headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        print(f"[watchlist] KV load failed status={exc.code}: {exc}")
    except Exception as exc:
        print(f"[watchlist] KV load failed: {exc}")
    return None


def save(payload: Dict[str, Any]) -> bool:
    if not configured():
        return False
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(_kv_url(), data=data, headers=_headers(), method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", "replace")
        response = json.loads(raw) if raw else {"success": True}
        ok = bool(response.get("success", True))
        if not ok:
            print(f"[watchlist] KV save rejected: {response}")
        return ok
    except Exception as exc:
        print(f"[watchlist] KV save failed: {exc}")
        return False


def _parse_updated_at(payload: Dict[str, Any]) -> Optional[dt.datetime]:
    value = payload.get("updated_at")
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def is_stale(payload: Optional[Dict[str, Any]], max_age_minutes: int) -> bool:
    if payload is None:
        return True
    updated_at = _parse_updated_at(payload)
    if not updated_at:
        return True
    age = dt.datetime.now(dt.timezone.utc) - updated_at.astimezone(dt.timezone.utc)
    return age > dt.timedelta(minutes=max(1, int(max_age_minutes)))


def events_by_day(payload: Optional[Dict[str, Any]]) -> Dict[dt.date, set[int]]:
    if not payload:
        return {}
    raw_days = payload.get("days") or {}
    if not isinstance(raw_days, dict):
        return {}
    out: Dict[dt.date, set[int]] = {}
    for day_s, raw_events in raw_days.items():
        try:
            day = dt.date.fromisoformat(str(day_s))
        except Exception:
            continue
        ids: set[int] = set()
        if isinstance(raw_events, list):
            for item in raw_events:
                value = item.get("event_id") if isinstance(item, dict) else item
                try:
                    ids.add(int(value))
                except Exception:
                    continue
        if ids:
            out[day] = ids
    return out


def build_payload(rows_by_day: Dict[dt.date, Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
    days: Dict[str, list[Dict[str, Any]]] = {}
    total = 0
    for day, rows in sorted(rows_by_day.items()):
        items: list[Dict[str, Any]] = []
        seen: set[int] = set()
        for row in rows:
            try:
                event_id = int(row["event_id"])
            except Exception:
                continue
            if event_id in seen:
                continue
            seen.add(event_id)
            items.append(
                {
                    "event_id": event_id,
                    "home_name": str(row.get("home_name") or ""),
                    "away_name": str(row.get("away_name") or ""),
                    "tournament_name": str(row.get("tournament_name") or ""),
                    "start_ts": row.get("start_ts"),
                }
            )
        if items:
            days[day.isoformat()] = items
            total += len(items)
    return {
        "version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total": total,
        "days": days,
    }
