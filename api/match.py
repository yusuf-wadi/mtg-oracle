"""
Vercel Python serverless function — /api/match

POST JSON body:
{
  "user": "waedi",
  "paste": "1x Sram, Senior Edificer\n1x Hellkite Charger\n...",
  "scoring": "keyword" | "tfidf" | "hybrid",
  "replacement_mode": "auto" | "reinforce" | "redundant",   # optional
  "source": "moxfield" | "archidekt" | "both",              # optional (default moxfield)
  "archidekt_user": "OtherName",                            # optional override
  "decks": ["paper", "counterint++"],                       # optional name filter
  "extra_decks": "https://moxfield.com/decks/<id> 22634482" # optional direct refs
}

Response JSON:
{ "ok": true, "markdown": "...", "elapsed_sec": 12.3 }
"""
from __future__ import annotations

import json
import os
import sys
import time
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Make `lib/` importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from lib import card_matcher as cm  # noqa: E402


def _run_match(payload: dict) -> dict:
    user = (payload.get("user") or "").strip()
    extra_decks_raw = payload.get("extra_decks") or ""
    extra_refs = cm.parse_deck_references(extra_decks_raw)
    if not user and not extra_refs:
        return {"ok": False, "error": "Provide a username or at least one direct deck URL/ID."}

    source = (payload.get("source") or "moxfield").strip().lower()
    if source not in ("moxfield", "archidekt", "both"):
        return {"ok": False, "error": f"Invalid source: {source!r}"}
    archidekt_user = (payload.get("archidekt_user") or "").strip() or user

    paste_text = payload.get("paste") or ""
    if not paste_text.strip():
        return {"ok": False, "error": "Missing 'paste' (card list)."}

    scoring = payload.get("scoring") or "keyword"
    if scoring not in ("keyword", "tfidf", "hybrid"):
        return {"ok": False, "error": f"Invalid scoring mode: {scoring}"}

    replacement_mode = payload.get("replacement_mode") or "auto"
    if replacement_mode not in ("auto", "reinforce", "redundant"):
        return {"ok": False, "error": f"Invalid replacement mode: {replacement_mode}"}

    if scoring in ("tfidf", "hybrid") and not cm._SKLEARN_AVAILABLE:
        return {"ok": False, "error": "scikit-learn unavailable for tfidf/hybrid mode."}

    deck_patterns = payload.get("decks") or []
    if isinstance(deck_patterns, str):
        deck_patterns = [p.strip() for p in deck_patterns.split(",") if p.strip()]

    # Load config: prefer config.example.json shipped with the project for the
    # expanded theme keywords; fall back to defaults.
    cfg_path = _ROOT / "config.example.json"
    cfg = cm.load_config(cfg_path if cfg_path.exists() else None)

    purchases = cm.load_purchases(paste_text)
    if not purchases:
        return {"ok": False, "error": "No cards parsed from paste body."}

    t0 = time.time()
    sources = ["moxfield", "archidekt"] if source == "both" else [source]
    user_decks: list[dict] = []
    source_errors: list[str] = []
    if user:
        for s in sources:
            u = archidekt_user if s == "archidekt" else user
            try:
                chunk = cm.list_user_decks_for(u, s)
            except RuntimeError as e:
                source_errors.append(f"{s} list failed: {e}")
                continue
            # Restrict Archidekt to Commander-format decks — our scoring assumes EDH.
            if s == "archidekt":
                chunk = [d for d in chunk if d.get("format") == cm._ARCHIDEKT_COMMANDER_FORMAT]
            user_decks.extend(chunk)

    # Apply name-pattern filter (if any) BEFORE adding direct deck refs — direct
    # refs are always honored regardless of the filter.
    if deck_patterns:
        user_decks = cm.filter_decks(user_decks, deck_patterns)

    # Track (source, public_id) pairs already in the list to avoid duplicates
    # when extra_decks overlaps with the user search.
    have_keys = {(d.get("_source") or "moxfield", d.get("publicId")) for d in user_decks}

    # Soft cap username-listed decks so we stay inside Vercel's 60s Hobby limit.
    MAX_DECKS = int(os.environ.get("MTG_ORACLE_MAX_DECKS", "8"))
    if len(user_decks) > MAX_DECKS:
        user_decks = user_decks[:MAX_DECKS]
        have_keys = {(d.get("_source") or "moxfield", d.get("publicId")) for d in user_decks}

    if not user_decks and not extra_refs:
        detail = ("; ".join(source_errors)) if source_errors else ""
        srcs = " + ".join(sources)
        return {"ok": False, "error": f"{srcs} user '{user}' has no public decks (or doesn't exist)." + (f" [{detail}]" if detail else "")}

    if deck_patterns and not user_decks and not extra_refs:
        return {"ok": False, "error": "No decks matched the provided deck filter."}

    full_decks = []
    fetch_errors: list[str] = []
    for d in user_decks:
        pid = d.get("publicId")
        src = d.get("_source") or "moxfield"
        if not pid:
            continue
        try:
            full_decks.append(cm.fetch_deck_for(pid, src))
            time.sleep(0.15)
        except RuntimeError as e:
            fetch_errors.append(f"{src}:{pid} {e}")
            continue

    # Now honor direct deck refs (e.g. Moxfield search-index quirks may drop
    # a deck that the user still wants in the report).
    for src, pid in extra_refs:
        if (src, pid) in have_keys:
            continue
        try:
            full_decks.append(cm.fetch_deck_for(pid, src))
            time.sleep(0.15)
        except RuntimeError as e:
            fetch_errors.append(f"{src}:{pid} {e}")
            continue

    if not full_decks:
        detail = ("; ".join(fetch_errors)) if fetch_errors else ""
        return {"ok": False, "error": "Could not fetch any deck details." + (f" [{detail}]" if detail else "")}

    # build_report writes to a file path; use a temp file (Vercel allows /tmp).
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
        out_path = Path(tf.name)
    try:
        cm.build_report(user, purchases, full_decks, out_path, cfg,
                        scoring_mode=scoring, replacement_mode=replacement_mode)
        markdown = out_path.read_text(encoding="utf-8")
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass

    return {
        "ok": True,
        "markdown": markdown,
        "elapsed_sec": round(time.time() - t0, 2),
        "decks_analyzed": len(full_decks),
        "purchases": len(purchases),
        "scoring": scoring,
        "replacement_mode": replacement_mode,
        "source": source,
        "extra_decks_count": len(extra_refs),
    }


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel requires lowercase
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # CORS preflight
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        self._send(200, {"ok": True, "service": "mtg-oracle", "endpoint": "POST /api/match"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            return self._send(400, {"ok": False, "error": f"Invalid JSON: {e}"})

        try:
            result = _run_match(payload)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        status = 200 if result.get("ok") else 400
        self._send(status, result)
