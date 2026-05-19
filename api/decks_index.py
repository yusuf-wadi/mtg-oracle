"""Vercel Python serverless function — /api/decks_index

Lightweight deck listing for the dashboard's "Load decks" picker. Returns
just enough metadata to populate a checklist; cards are NOT fetched.

POST JSON body:
{
  "user": "waedi",
  "source": "moxfield" | "archidekt" | "both",
  "archidekt_user": "Wildcard"  // optional, used when source == "both"
}

Response:
{
  "ok": true,
  "decks": [
    {"source": "moxfield", "publicId": "abc...", "name": "...", "colors": "BRW", "url": "https://..."}
  ],
  "source_errors": [],
  "elapsed_sec": 0.42
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

from lib import card_matcher as cm  # noqa: E402


def _color_string(stub: dict) -> str:
    """Best-effort color identity string from a deck stub.

    Moxfield stubs expose ``colorIdentity`` as a list of letters; Archidekt
    stubs are normalized to the same shape in card_matcher.list_archidekt_decks.
    """
    ci = stub.get("colorIdentity") or []
    if isinstance(ci, str):
        # Moxfield sometimes returns a string like "BRW"
        ci = list(ci)
    letters = [c for c in ci if c in ("W", "U", "B", "R", "G")]
    return "".join(sorted(letters)) if letters else "C"


def _run(payload: dict) -> dict:
    user = (payload.get("user") or "").strip()
    if not user:
        return {"ok": False, "error": "Provide a username to list decks."}

    source = (payload.get("source") or "moxfield").strip().lower()
    if source not in ("moxfield", "archidekt", "both"):
        return {"ok": False, "error": f"Invalid source: {source!r}"}
    archidekt_user = (payload.get("archidekt_user") or "").strip() or user

    sources = ["moxfield", "archidekt"] if source == "both" else [source]
    decks: list[dict] = []
    source_errors: list[str] = []

    t0 = time.time()
    for s in sources:
        u = archidekt_user if s == "archidekt" else user
        try:
            chunk = cm.list_user_decks_for(u, s)
        except RuntimeError as e:
            source_errors.append(f"{s}: {e}")
            continue
        if s == "archidekt":
            chunk = [d for d in chunk if d.get("format") == cm._ARCHIDEKT_COMMANDER_FORMAT]
        for stub in chunk:
            pid = stub.get("publicId")
            name = stub.get("name") or pid or "Untitled"
            if not pid:
                continue
            decks.append({
                "source": s,
                "publicId": str(pid),
                "name": str(name),
                "colors": _color_string(stub),
                "url": stub.get("publicUrl") or "",
            })

    return {
        "ok": True,
        "user": user,
        "archidekt_user": archidekt_user,
        "decks": decks,
        "source_errors": source_errors,
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
        self._send(200, {"ok": True, "service": "mtg-oracle", "endpoint": "POST /api/decks_index"})

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
