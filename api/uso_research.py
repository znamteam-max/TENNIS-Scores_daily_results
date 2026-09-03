from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import re
import statistics
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

from providers import sofascore as ss
from providers.flashscore_odds import PARTICIPANTS_RE


def _norm(value: Any) -> str:
    text = " ".join(str(value or "").lower().replace("ё", "е").split())
    return text


def _is_uso(event: Dict[str, Any]) -> bool:
    raw = event.get("raw") or {}
    hay = _norm(" ".join([
        str(event.get("tournament_name") or ""),
        str(event.get("season_name") or ""),
        str(raw.get("flashscore_league") or ""),
    ]))
    if not any(token in hay for token in ("us open", "открытый чемпионат сша", "сша open")):
        return False
    # Adult singles only.
    blocked = ("doubles", "парн", "mixed", "микст", "junior", "юниор", "boys", "girls")
    if any(token in hay for token in blocked):
        return False
    return event.get("tour_group") in {"men", "women"}


def _phase(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    hay = _norm(" ".join([
        str(event.get("tournament_name") or ""),
        str(event.get("season_name") or ""),
        str(raw.get("flashscore_league") or ""),
    ]))
    return "qualifying" if any(x in hay for x in ("qualif", "квалиф")) else "main"


def _stat(stats: Dict[str, Dict[str, str]], names: Iterable[str]) -> Dict[str, str]:
    wanted = {_norm(name) for name in names}
    for key, row in stats.items():
        if _norm(key) in wanted:
            return row or {}
    for key, row in stats.items():
        nk = _norm(key)
        if any(name in nk or nk in name for name in wanted if len(name) >= 5):
            return row or {}
    return {}


def _side_value(row: Dict[str, str], side: str) -> str:
    return str(row.get(side) or "")


def _pct(value: Any) -> Optional[float]:
    text = str(value or "")
    m = re.search(r"(-?\d+(?:[\.,]\d+)?)\s*%", text)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m and int(m.group(2)):
        return 100.0 * int(m.group(1)) / int(m.group(2))
    return None


def _fraction(value: Any) -> Tuple[Optional[int], Optional[int]]:
    text = str(value or "")
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if not m:
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", text)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _number(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    # For percentage+fraction stats, the numerator is the useful count.
    num, den = _fraction(text)
    if num is not None and den is not None:
        return float(num)
    m = re.search(r"-?\d+(?:[\.,]\d+)?", text)
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def _participant_ranks(page: str) -> Tuple[Optional[int], Optional[int]]:
    match = PARTICIPANTS_RE.search(page or "")
    if not match:
        return None, None
    try:
        data = json.loads(match.group(1))
    except Exception:
        return None, None

    def rank(side: str) -> Optional[int]:
        obj = ((data.get(side) or [{}])[0] or {})
        value = obj.get("rank")
        if isinstance(value, list):
            candidates = [x for x in value if isinstance(x, (int, float)) or str(x).isdigit()]
            if candidates:
                try:
                    return int(candidates[-1])
                except Exception:
                    return None
        if isinstance(value, (int, float)) or str(value or "").isdigit():
            return int(value)
        return None

    return rank("home"), rank("away")


def _metric_rows(stats: Dict[str, Dict[str, str]], side: str) -> Dict[str, Optional[float]]:
    rows = {
        "aces": _stat(stats, ("Aces", "Подачи навылет", "Эйсы")),
        "double_faults": _stat(stats, ("Double Faults", "Двойные ошибки")),
        "first_serve_pct": _stat(stats, ("1st Serve Percentage", "1-я подача", "Процент первой подачи")),
        "first_serve_won_pct": _stat(stats, ("1st serve points won", "Очки выигр. на п.п.", "Выиграно очков на 1-й подаче")),
        "second_serve_won_pct": _stat(stats, ("2nd serve points won", "Очки выигр. на в.п.", "Выиграно очков на 2-й подаче")),
        "break_points_converted": _stat(stats, ("Break Points Converted", "Реализованные брейкпойнты", "Брейк-пойнты")),
        "break_points_saved": _stat(stats, ("Break Points Saved", "Спасенные брейкпойнты", "Отбитые брейк-пойнты")),
        "winners": _stat(stats, ("Winners", "Активно выигр. мячи", "Виннерсы")),
        "unforced_errors": _stat(stats, ("Unforced errors", "Невынужд. ошибки", "Невынужденные ошибки")),
        "total_points_won": _stat(stats, ("Total Points Won", "Всего выигранных очков", "Выиграно очков")),
        "net_points_won": _stat(stats, ("Net Points Won", "Очки выигранные у сетки", "Выходы к сетке")),
    }
    out: Dict[str, Optional[float]] = {}
    for key, row in rows.items():
        value = _side_value(row, side)
        if key.endswith("_pct"):
            out[key] = _pct(value)
        elif key in {"break_points_converted", "break_points_saved", "net_points_won"}:
            won, total = _fraction(value)
            out[f"{key}_won"] = float(won) if won is not None else None
            out[f"{key}_total"] = float(total) if total is not None else None
            out[f"{key}_pct"] = _pct(value)
        else:
            out[key] = _number(value)

    # Exact service-point denominators from FS fractions, when available.
    f1_w, f1_t = _fraction(_side_value(rows["first_serve_won_pct"], side))
    f2_w, f2_t = _fraction(_side_value(rows["second_serve_won_pct"], side))
    if f1_w is not None and f1_t and f2_w is not None and f2_t:
        service_total = f1_t + f2_t
        service_won = f1_w + f2_w
        out["service_points"] = float(service_total)
        out["service_points_won_pct"] = 100.0 * service_won / service_total if service_total else None
        aces = out.get("aces")
        dfs = out.get("double_faults")
        out["ace_rate"] = 100.0 * aces / service_total if aces is not None and service_total else None
        out["double_fault_rate"] = 100.0 * dfs / service_total if dfs is not None and service_total else None
    else:
        out["service_points"] = None
        out["service_points_won_pct"] = None
        out["ace_rate"] = None
        out["double_fault_rate"] = None
    return out


def _derive_pair(home: Dict[str, Optional[float]], away: Dict[str, Optional[float]]) -> None:
    ht = home.get("total_points_won")
    at = away.get("total_points_won")
    total_played = (ht + at) if ht is not None and at is not None else None
    for current, opponent in ((home, away), (away, home)):
        if opponent.get("service_points_won_pct") is not None:
            current["return_points_won_pct"] = 100.0 - float(opponent["service_points_won_pct"])
        else:
            current["return_points_won_pct"] = None
        if total_played and total_played > 0:
            w = current.get("winners")
            ue = current.get("unforced_errors")
            current["winner_rate"] = 100.0 * w / total_played if w is not None else None
            current["ue_rate"] = 100.0 * ue / total_played if ue is not None else None
            current["aggression_volume"] = 100.0 * (w + ue) / total_played if w is not None and ue is not None else None
            current["aggression_quality"] = 100.0 * (w - ue) / total_played if w is not None and ue is not None else None
        else:
            current["winner_rate"] = current["ue_rate"] = None
            current["aggression_volume"] = current["aggression_quality"] = None


def _q(values: List[float], p: float) -> Optional[float]:
    clean = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = (len(clean) - 1) * p
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)


def _avg(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return statistics.fmean(clean) if clean else None


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _trait_report(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    metrics = ("ace_rate", "first_serve_won_pct", "second_serve_won_pct", "service_points_won_pct", "return_points_won_pct", "aggression_volume", "aggression_quality", "ue_rate")
    qs: Dict[str, Dict[str, Optional[float]]] = {}
    for metric in metrics:
        vals = [o[metric] for o in observations if o.get(metric) is not None]
        qs[metric] = {"q25": _q(vals, .25), "median": _q(vals, .5), "q75": _q(vals, .75)}

    def ge(o: Dict[str, Any], key: str, threshold: Optional[float]) -> bool:
        return threshold is not None and o.get(key) is not None and float(o[key]) >= threshold

    def le(o: Dict[str, Any], key: str, threshold: Optional[float]) -> bool:
        return threshold is not None and o.get(key) is not None and float(o[key]) <= threshold

    traits = {
        "big_server": lambda o: ge(o, "ace_rate", qs["ace_rate"]["q75"]),
        "elite_first_serve": lambda o: ge(o, "first_serve_won_pct", qs["first_serve_won_pct"]["q75"]),
        "strong_second_serve": lambda o: ge(o, "second_serve_won_pct", qs["second_serve_won_pct"]["q75"]),
        "return_pressure": lambda o: ge(o, "return_points_won_pct", qs["return_points_won_pct"]["q75"]),
        "high_aggression_volume": lambda o: ge(o, "aggression_volume", qs["aggression_volume"]["q75"]),
        "controlled_aggression": lambda o: ge(o, "aggression_volume", qs["aggression_volume"]["median"]) and ge(o, "aggression_quality", qs["aggression_quality"]["q75"]),
        "reckless_aggression": lambda o: ge(o, "aggression_volume", qs["aggression_volume"]["q75"]) and le(o, "aggression_quality", qs["aggression_quality"]["q25"]),
        "low_error_counterpunch": lambda o: le(o, "ue_rate", qs["ue_rate"]["q25"]) and le(o, "aggression_volume", qs["aggression_volume"]["median"]),
        "serve_plus_return": lambda o: ge(o, "service_points_won_pct", qs["service_points_won_pct"]["q75"]) and ge(o, "return_points_won_pct", qs["return_points_won_pct"]["q75"]),
    }

    report: Dict[str, Any] = {"thresholds": {k: {q: _round(v) for q, v in row.items()} for k, row in qs.items()}, "traits": {}}
    for name, predicate in traits.items():
        rows = [o for o in observations if predicate(o)]
        wins = sum(1 for o in rows if o.get("won"))
        report["traits"][name] = {
            "player_matches": len(rows),
            "wins": wins,
            "win_rate": _round(100.0 * wins / len(rows), 1) if rows else None,
            "examples": [
                {
                    "player": o["player"], "opponent": o["opponent"], "won": bool(o["won"]),
                    "phase": o["phase"], "score": o["score"],
                    "rank": o.get("rank"), "opp_rank": o.get("opp_rank"),
                    "ace_rate": _round(o.get("ace_rate")),
                    "first_serve_won_pct": _round(o.get("first_serve_won_pct")),
                    "second_serve_won_pct": _round(o.get("second_serve_won_pct")),
                    "return_points_won_pct": _round(o.get("return_points_won_pct")),
                    "aggression_volume": _round(o.get("aggression_volume")),
                    "aggression_quality": _round(o.get("aggression_quality")),
                    "winners": o.get("winners"), "unforced_errors": o.get("unforced_errors"),
                }
                for o in sorted(rows, key=lambda x: (bool(x.get("won")), x.get("aggression_quality") or -999, x.get("return_points_won_pct") or -999), reverse=True)[:6]
            ],
        }
    return report


def _match_advantages(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    configs = {
        "more_aggressive_volume_5pp": ("aggression_volume", 5.0, True),
        "better_aggression_quality_5pp": ("aggression_quality", 5.0, True),
        "better_second_serve_8pp": ("second_serve_won_pct", 8.0, True),
        "bigger_ace_rate_3pp": ("ace_rate", 3.0, True),
        "lower_ue_rate_3pp": ("ue_rate", 3.0, False),
    }
    out = {}
    for label, (metric, min_gap, higher_is_candidate) in configs.items():
        cases = 0
        candidate_wins = 0
        examples = []
        for match in matches:
            h, a = match["home_metrics"], match["away_metrics"]
            hv, av = h.get(metric), a.get(metric)
            if hv is None or av is None or abs(float(hv) - float(av)) < min_gap:
                continue
            candidate_side = "home" if ((hv > av) == higher_is_candidate) else "away"
            won = candidate_side == match["winner_side"]
            cases += 1
            candidate_wins += int(won)
            if len(examples) < 8 or won:
                examples.append({
                    "candidate": match[f"{candidate_side}_name"],
                    "opponent": match["away_name" if candidate_side == "home" else "home_name"],
                    "won": won,
                    "candidate_value": _round(hv if candidate_side == "home" else av),
                    "opponent_value": _round(av if candidate_side == "home" else hv),
                    "phase": match["phase"],
                    "score": match["score"],
                })
        out[label] = {
            "cases": cases,
            "candidate_wins": candidate_wins,
            "win_rate": _round(100.0 * candidate_wins / cases, 1) if cases else None,
            "examples": examples[:8],
        }
    return out


def _player_rollup(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for o in observations:
        bucket[o["player"]].append(o)
    rows = []
    for player, obs in bucket.items():
        wins = sum(1 for o in obs if o["won"])
        rows.append({
            "player": player,
            "matches": len(obs),
            "wins": wins,
            "losses": len(obs) - wins,
            "win_rate": _round(100 * wins / len(obs), 1),
            "avg_rank": _round(_avg(o.get("rank") for o in obs), 0),
            "ace_rate": _round(_avg(o.get("ace_rate") for o in obs)),
            "first_serve_won_pct": _round(_avg(o.get("first_serve_won_pct") for o in obs)),
            "second_serve_won_pct": _round(_avg(o.get("second_serve_won_pct") for o in obs)),
            "return_points_won_pct": _round(_avg(o.get("return_points_won_pct") for o in obs)),
            "aggression_volume": _round(_avg(o.get("aggression_volume") for o in obs)),
            "aggression_quality": _round(_avg(o.get("aggression_quality") for o in obs)),
            "winner_rate": _round(_avg(o.get("winner_rate") for o in obs)),
            "ue_rate": _round(_avg(o.get("ue_rate") for o in obs)),
        })
    rows.sort(key=lambda r: (r["wins"], r["matches"], r.get("aggression_quality") or -999), reverse=True)
    return rows


def _upsets(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for match in matches:
        side = match["winner_side"]
        loser = "away" if side == "home" else "home"
        wrank = match.get(f"{side}_rank")
        lrank = match.get(f"{loser}_rank")
        if wrank is None or lrank is None or wrank <= lrank:
            continue
        wm = match[f"{side}_metrics"]
        lm = match[f"{loser}_metrics"]
        rows.append({
            "winner": match[f"{side}_name"], "loser": match[f"{loser}_name"],
            "winner_rank": wrank, "loser_rank": lrank, "rank_gap": wrank - lrank,
            "group": match["group"], "phase": match["phase"], "score": match["score"],
            "winner_ace_rate": _round(wm.get("ace_rate")), "loser_ace_rate": _round(lm.get("ace_rate")),
            "winner_1st": _round(wm.get("first_serve_won_pct")), "loser_1st": _round(lm.get("first_serve_won_pct")),
            "winner_2nd": _round(wm.get("second_serve_won_pct")), "loser_2nd": _round(lm.get("second_serve_won_pct")),
            "winner_return": _round(wm.get("return_points_won_pct")), "loser_return": _round(lm.get("return_points_won_pct")),
            "winner_aggr_volume": _round(wm.get("aggression_volume")), "loser_aggr_volume": _round(lm.get("aggression_volume")),
            "winner_aggr_quality": _round(wm.get("aggression_quality")), "loser_aggr_quality": _round(lm.get("aggression_quality")),
            "winner_winners": wm.get("winners"), "winner_ue": wm.get("unforced_errors"),
            "loser_winners": lm.get("winners"), "loser_ue": lm.get("unforced_errors"),
        })
    rows.sort(key=lambda r: r["rank_gap"], reverse=True)
    return rows[:30]


async def _load_day(day: dt.date) -> List[Dict[str, Any]]:
    data = await ss.flashscore_events_by_date(day)
    rows = ss.normalize_events(data)
    return [e for e in rows if _is_uso(e) and ss.has_result_winner(e)]


async def _collect(date_from: dt.date, date_to: dt.date, concurrency: int = 14) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    days = [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    by_day = await asyncio.gather(*(_load_day(day) for day in days))
    events: List[Dict[str, Any]] = []
    seen = set()
    for rows in by_day:
        for event in rows:
            match_id = str((event.get("raw") or {}).get("flashscore_id") or event.get("custom_id") or "")
            if not match_id or match_id in seen:
                continue
            seen.add(match_id)
            events.append(event)

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 24)))
    timeout = httpx.Timeout(18.0)
    matches: List[Dict[str, Any]] = []
    failures = 0

    async with httpx.AsyncClient(http2=False, timeout=timeout, follow_redirects=True) as client:
        async def one(event: Dict[str, Any]) -> None:
            nonlocal failures
            raw = event.get("raw") or {}
            match_id = str(raw.get("flashscore_id") or event.get("custom_id") or "")
            referer = f"{ss.FLASHSCORE_BASE}/match/{match_id}/"
            headers = {
                "Accept": "*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
                "Referer": referer,
                "User-Agent": ss.UAS[0],
                "x-fsign": ss.FLASHSCORE_FSIGN,
            }
            async with semaphore:
                try:
                    stats_resp, page_resp = await asyncio.gather(
                        client.get(f"{ss.FLASHSCORE_BASE}/x/feed/df_st_2_{match_id}", headers=headers),
                        client.get(referer, headers={k: v for k, v in headers.items() if k != "x-fsign"}),
                    )
                    stats_text = stats_resp.text if stats_resp.status_code == 200 else ""
                    page = page_resp.text if page_resp.status_code == 200 else ""
                except Exception:
                    failures += 1
                    return
            stats = ss._parse_stats(stats_text)  # type: ignore[attr-defined]
            if not stats:
                failures += 1
                return
            home_metrics = _metric_rows(stats, "home")
            away_metrics = _metric_rows(stats, "away")
            _derive_pair(home_metrics, away_metrics)
            home_rank, away_rank = _participant_ranks(page)
            winner_code = str(raw.get("winnerCode") or "")
            if winner_code not in {"1", "2"}:
                failures += 1
                return
            matches.append({
                "match_id": match_id,
                "group": event.get("tour_group"),
                "phase": _phase(event),
                "tournament": event.get("tournament_name"),
                "home_name": event.get("home_name"),
                "away_name": event.get("away_name"),
                "home_rank": home_rank,
                "away_rank": away_rank,
                "winner_side": "home" if winner_code == "1" else "away",
                "score": ss.compact_score(event),
                "home_metrics": home_metrics,
                "away_metrics": away_metrics,
            })

        await asyncio.gather(*(one(event) for event in events))

    meta = {
        "dates": [d.isoformat() for d in days],
        "uso_finished_events_found": len(events),
        "matches_with_flashscore_stats": len(matches),
        "stats_failures_or_missing": failures,
    }
    return matches, meta


def _observations(matches: List[Dict[str, Any]], group: Optional[str] = None, phase: Optional[str] = None) -> List[Dict[str, Any]]:
    out = []
    for match in matches:
        if group and match["group"] != group:
            continue
        if phase and match["phase"] != phase:
            continue
        for side in ("home", "away"):
            opponent = "away" if side == "home" else "home"
            row = dict(match[f"{side}_metrics"])
            row.update({
                "player": match[f"{side}_name"],
                "opponent": match[f"{opponent}_name"],
                "rank": match.get(f"{side}_rank"),
                "opp_rank": match.get(f"{opponent}_rank"),
                "won": side == match["winner_side"],
                "group": match["group"], "phase": match["phase"], "score": match["score"],
            })
            out.append(row)
    return out


def _report(matches: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"ok": True, "meta": meta, "segments": {}, "top_upsets": _upsets(matches)}
    for group in ("men", "women"):
        group_matches = [m for m in matches if m["group"] == group]
        obs = _observations(group_matches)
        report["segments"][group] = {
            "matches": len(group_matches),
            "qualifying_matches": sum(1 for m in group_matches if m["phase"] == "qualifying"),
            "main_matches": sum(1 for m in group_matches if m["phase"] == "main"),
            "traits": _trait_report(obs),
            "head_to_head_style_advantages": _match_advantages(group_matches),
            "player_rollup": _player_rollup(obs)[:40],
        }
        for phase in ("qualifying", "main"):
            phase_matches = [m for m in group_matches if m["phase"] == phase]
            if phase_matches:
                report["segments"][f"{group}_{phase}"] = {
                    "matches": len(phase_matches),
                    "traits": _trait_report(_observations(phase_matches)),
                    "head_to_head_style_advantages": _match_advantages(phase_matches),
                }
    return report


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            start = dt.date.fromisoformat(q.get("from", ["2026-08-24"])[0])
            end = dt.date.fromisoformat(q.get("to", [dt.date.today().isoformat()])[0])
            if end < start or (end - start).days > 20:
                raise ValueError("date range must be 0..20 days")
            concurrency = int(q.get("concurrency", ["14"])[0])
            matches, meta = asyncio.run(_collect(start, end, concurrency=concurrency))
            payload = _report(matches, meta)
            if q.get("rows", ["0"])[0].lower() in {"1", "true", "yes"}:
                payload["matches"] = matches
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
