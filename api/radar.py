"""Vercel Python serverless function — /api/radar

Resolves decks from the dashboard's shared deck-source payload and scores
each one across the 105 MTG mechanical axes (12 families).

POST JSON body (same shape as /api/match):
{
  "user": "...",
  "source": "moxfield" | "archidekt" | "both",
  "archidekt_user": "...",
  "decks": ["paper", ...],
  "extra_decks": "..."
}

Response:
{
  "ok": true,
  "families": [{id, label, axes: [{id}]}],
  "axes_total": 105,
  "decks": [
    {name, cards, color_identity, family_scores: {fam_id: score}, axis_scores: {axis_id: score}}
  ],
  "elapsed_sec": 1.2
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
from lib import radar as rd  # noqa: E402


def _run(payload: dict) -> dict:
    try:
        full_decks, meta = ds.resolve_decks(payload)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    t0 = time.time()
    fams = rd.families()
    axes_total = sum(len(f["axes"]) for f in fams)

    deck_results = []
    for deck in full_decks:
        corpus = ds.deck_oracle_corpus(deck)
        axis_scores, family_scores, total = rd.score_deck(corpus)
        deck_results.append({
            "name": ds.deck_display_name(deck),
            "source": deck.get("_source") or "moxfield",
            "publicId": deck.get("publicId"),
            "cards": total,
            "color_identity": ds.deck_color_identity(deck),
            "family_scores": family_scores,
            "axis_scores": axis_scores,
        })

    return {
        "ok": True,
        "families": fams,
        "axes_total": axes_total,
        "decks": deck_results,
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
        self._send(200, {"ok": True, "service": "mtg-oracle", "endpoint": "POST /api/radar"})

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
