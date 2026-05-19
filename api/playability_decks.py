"""Vercel Python serverless function — /api/playability_decks

Resolves decks from the dashboard's shared deck-source payload (username +
extras + filters) and runs the Monte Carlo playability simulator on each.

POST JSON body:
{
  "user": "waedi",
  "source": "moxfield" | "archidekt" | "both",
  "archidekt_user": "...",
  "decks": ["paper", ...],
  "extra_decks": "https://moxfield.com/decks/<id>",
  "simulations": 10000,
  "turns_seen": 3
}

Response:
{
  "ok": true,
  "decks": [{name, result: {...analyze() output...}}],
  "simulations": 10000,
  "turns_seen": 3,
  "elapsed_sec": 12.3
}
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from lib import deck_source as ds  # noqa: E402
from lib.playability import analyze  # noqa: E402


SIM_MAX = 25000
SIM_MIN = 200
TURNS_MIN = 1
TURNS_MAX = 6


def _run(payload: dict) -> dict:
    try:
        full_decks, meta = ds.resolve_decks(payload)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        simulations = int(payload.get("simulations") or 10000)
    except (TypeError, ValueError):
        simulations = 10000
    simulations = max(SIM_MIN, min(SIM_MAX, simulations))

    try:
        turns_seen = int(payload.get("turns_seen") or 3)
    except (TypeError, ValueError):
        turns_seen = 3
    turns_seen = max(TURNS_MIN, min(TURNS_MAX, turns_seen))

    t0 = time.time()
    deck_results = []
    for deck in full_decks:
        decklist = ds.deck_decklist_text(deck)
        if not decklist.strip():
            deck_results.append({
                "name": ds.deck_display_name(deck),
                "error": "Empty deck after extraction.",
            })
            continue
        try:
            result = analyze(decklist, simulations=simulations, turns_seen=turns_seen)
        except Exception as e:  # noqa: BLE001
            deck_results.append({
                "name": ds.deck_display_name(deck),
                "error": f"{type(e).__name__}: {e}",
            })
            continue
        deck_results.append({
            "name": ds.deck_display_name(deck),
            "source": deck.get("_source") or "moxfield",
            "publicId": deck.get("publicId"),
            "result": result,
        })

    return {
        "ok": True,
        "decks": deck_results,
        "simulations": simulations,
        "turns_seen": turns_seen,
        "source": meta.get("source"),
        "fetch_errors": meta.get("fetch_errors", []),
        "elapsed_sec": round(time.time() - t0, 2),
    }


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        self._send(200, {"ok": True, "service": "mtg-oracle", "endpoint": "POST /api/playability_decks"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            return self._send(400, {"ok": False, "error": f"Invalid JSON: {e}"})

        try:
            result = _run(payload)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        status = 200 if result.get("ok") else 400
        self._send(status, result)
    def log_message(self, format, *args):
        pass
