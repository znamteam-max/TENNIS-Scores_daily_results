from __future__ import annotations

from http.server import BaseHTTPRequestHandler

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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = build_match_card_png(SAMPLE_EVENT)
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
