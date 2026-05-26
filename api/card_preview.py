from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import sitecustomize as _match_card_startup_patch  # noqa: F401
from match_card import build_match_card_png


SAMPLE_EVENT = {
    "category": "WTA 1000",
    "tournament_status": "WTA 1000",
    "tournament_name": "Мадрид",
    "home_name": "Соболенко А.",
    "away_name": "Осака Н.",
    "raw": {
        "winnerCode": 1,
        "flashscore_round": "1/16",
        "homeScore": {"current": 2, "period1": 6, "period2": 6, "period3": 6},
        "awayScore": {"current": 1, "period1": 7, "period2": 3, "period3": 2},
    },
}


def _grand_slam_sample(sets: str) -> dict:
    raw = {
        "winnerCode": 1,
        "flashscore_round": "1/64 финала",
        "status": {"type": "finished"},
        "homeScore": {"current": 3},
        "awayScore": {"current": 2},
    }
    home_sets = [6, 7, 6, 6, 7] if sets == "5" else [5, 7, 6, 6]
    away_sets = [4, 6, 7, 7, 5] if sets == "5" else [7, 5, 1, 4]
    raw["homeScore"].update({f"period{idx}": value for idx, value in enumerate(home_sets, start=1)})
    raw["awayScore"].update({f"period{idx}": value for idx, value in enumerate(away_sets, start=1)})
    return {
        "category": "ATP",
        "tournament_status": "Grand Slam",
        "tournament_sort_rank": 0,
        "tour_group": "men",
        "tournament_name": "Ролан Гаррос",
        "season_name": "Roland Garros",
        "home_name": "Алькарас",
        "away_name": "Зверев",
        "raw": raw,
    }


def _french_open_flashscore_sample(sets: str) -> dict:
    event = _grand_slam_sample(sets)
    event.pop("tournament_sort_rank", None)
    event["tournament_status"] = "ATP 250"
    event["tournament_name"] = "Открытый чемпионат Франции (Франция)"
    event["season_name"] = "ATP - Одиночный разряд"
    event["home_name"] = "Рууд К."
    event["away_name"] = "Сафиуллин Р."
    if sets == "5":
        event["raw"]["homeScore"].update({"current": 3, "period1": 6, "period2": 7, "period3": 5, "period4": 0, "period5": 6})
        event["raw"]["awayScore"].update({"current": 2, "period1": 2, "period2": 6, "period3": 7, "period4": 6, "period5": 2})
    return event


def _apply_preview_case(event: dict, case: str) -> dict:
    raw = event.setdefault("raw", {})
    if case in {"davidovich", "long"}:
        event["home_name"] = "Давидович Фокина А."
        event["away_name"] = "Джумхур Д."
        raw["winnerCode"] = 1
        raw["homeScore"] = {"current": 3, "period1": 6, "period2": 6, "period3": 2, "period4": 7, "period5": 6}
        raw["awayScore"] = {"current": 2, "period1": 7, "period2": 3, "period3": 6, "period4": 5, "period5": 3}
    elif case == "medjedovic":
        event["home_name"] = "Меджедович Х."
        event["away_name"] = "Ханфманн Я."
        raw["winnerCode"] = 1
        raw["homeScore"] = {"current": 3, "period1": 6, "period2": 6, "period3": 6, "period4": 6}
        raw["awayScore"] = {"current": 1, "period1": 3, "period2": 4, "period3": 7, "period4": 4}
    return event


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            sets = query.get("sets", ["3"])[0]
            if query.get("french", ["0"])[0] in {"1", "true", "yes"}:
                event = _french_open_flashscore_sample(sets)
            elif query.get("gs", ["0"])[0] in {"1", "true", "yes"}:
                event = _grand_slam_sample(sets)
            else:
                event = dict(SAMPLE_EVENT)
                event["raw"] = dict(SAMPLE_EVENT["raw"])
            if sets == "2":
                event["raw"]["homeScore"] = {"current": 2, "period1": 6, "period2": 6}
                event["raw"]["awayScore"] = {"current": 0, "period1": 4, "period2": 3}
            event = _apply_preview_case(event, query.get("case", [""])[0])
            body = build_match_card_png(event)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = str(exc).encode("utf-8", "replace")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
