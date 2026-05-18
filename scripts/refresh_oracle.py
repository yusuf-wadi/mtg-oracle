#!/usr/bin/env python3
"""
Refresh the slim Scryfall oracle index used by card_matcher.

Fetches Scryfall's `oracle_cards` bulk-data file, keeps only the fields the
matcher needs, gzips it, and writes:

  data/oracle-slim.json.gz   # the slim, gzipped card list
  data/oracle-meta.json      # download_uri, updated_at, card counts, sha256

Run locally or via CI. Scryfall asks bulk data to be re-fetched at most once
per 24h; we typically only need a refresh every few months or when a new set
drops.

Usage:
    python scripts/refresh_oracle.py            # refresh if remote is newer
    python scripts/refresh_oracle.py --force    # always re-download
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

BULK_INDEX_URL = "https://api.scryfall.com/bulk-data"
USER_AGENT = "card-matcher/2.0 (+https://github.com/yusuf-wadi/mtg-oracle)"

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
SLIM_PATH = DATA_DIR / "oracle-slim.json.gz"
META_PATH = DATA_DIR / "oracle-meta.json"

# Fields we keep from each Scryfall card. Everything else (image URIs, set
# data, prices, rulings, etc.) is dropped to keep the artifact small.
KEEP_FIELDS = ("name", "type_line", "oracle_text", "color_identity",
               "cmc", "legalities", "layout")
FACE_FIELDS = ("name", "type_line", "oracle_text")


def _http_json(url: str) -> dict:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _http_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=300) as r:
        return r.read()


def _slim_card(c: dict) -> dict:
    out = {k: c.get(k) for k in KEEP_FIELDS}
    faces = c.get("card_faces") or []
    if faces:
        out["card_faces"] = [{k: f.get(k) for k in FACE_FIELDS} for f in faces]
    return out


def find_oracle_entry(manifest: dict) -> dict:
    for it in manifest.get("data", []):
        if it.get("type") == "oracle_cards":
            return it
    raise RuntimeError("oracle_cards entry not found in bulk-data manifest")


def load_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text())
        except Exception:
            return {}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if the local copy is already up to date.")
    ap.add_argument("--out-dir", default=str(DATA_DIR),
                    help="Override output directory (default: ./data).")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slim_path = out_dir / "oracle-slim.json.gz"
    meta_path = out_dir / "oracle-meta.json"

    print(f"Fetching bulk-data manifest: {BULK_INDEX_URL}", file=sys.stderr)
    manifest = _http_json(BULK_INDEX_URL)
    entry = find_oracle_entry(manifest)

    download_uri = entry["download_uri"]
    updated_at = entry.get("updated_at")
    size_bytes = entry.get("size")
    print(f"Remote oracle_cards: updated_at={updated_at}  size={size_bytes} bytes",
          file=sys.stderr)
    print(f"  uri: {download_uri}", file=sys.stderr)

    prev = load_meta()
    if not args.force and prev.get("updated_at") == updated_at and slim_path.exists():
        print("Local copy is already up to date — nothing to do.", file=sys.stderr)
        return 0

    print("Downloading oracle bulk file...", file=sys.stderr)
    t0 = time.time()
    raw = _http_bytes(download_uri)
    print(f"  downloaded {len(raw)/1e6:.1f} MB in {time.time()-t0:.1f}s",
          file=sys.stderr)

    cards = json.loads(raw)
    print(f"  parsed {len(cards)} cards", file=sys.stderr)

    slim = [_slim_card(c) for c in cards]
    payload = json.dumps(slim, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    print(f"  slim raw: {len(payload)/1e6:.1f} MB", file=sys.stderr)

    with gzip.open(slim_path, "wb", compresslevel=9) as f:
        f.write(payload)
    slim_size = slim_path.stat().st_size
    print(f"  wrote {slim_path}  ({slim_size/1e6:.1f} MB gz)", file=sys.stderr)

    sha = hashlib.sha256(slim_path.read_bytes()).hexdigest()
    meta = {
        "source": "scryfall_bulk_oracle_cards",
        "download_uri": download_uri,
        "updated_at": updated_at,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "card_count": len(cards),
        "slim_bytes": slim_size,
        "slim_sha256": sha,
        "kept_fields": list(KEEP_FIELDS) + ["card_faces"],
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"  wrote {meta_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
