#!/usr/bin/env python3
"""
card_matcher.py — Match a purchase list against your Moxfield decks.

Pulls deck lists for a Moxfield user, then for each card on your purchase list reports:
  1) DIRECT HITS — decks where the card already appears (fuzzy-matched).
  2) UPGRADE CANDIDATES — decks where the card is color-legal AND Commander-legal
     (not banned) AND shares card types / theme keywords with cards in the deck.

USAGE
-----
    # CSV file
    python card_matcher.py --user waedi --purchases purchases.csv --out report.md

    # Paste directly (one card per line, optional "2x" prefix). End with EOF (Ctrl-D).
    python card_matcher.py --user waedi --paste

    # Pipe from stdin
    echo "Rhystic Study\\nDockside Extortionist" | python card_matcher.py --user waedi --paste

    # With a config file
    python card_matcher.py --user waedi --paste --config config.json

PURCHASE INPUT FORMATS
----------------------
CSV (only `name` is required):
    name,quantity,price,set,notes
    Smothering Tithe,1,29.99,RNA,for Shorikai

Plain text — one card per line, optional `2x` or `2 ` prefix:
    2x Lightning Bolt
    Rhystic Study
    Cyclonic Rift

CONFIG FILE (optional, JSON)
----------------------------
See `config.example.json`. All fields are optional and merged on top of defaults.

Requires: requests  (pip install requests)
"""
from __future__ import annotations

import argparse
import csv
import difflib
import fnmatch
import json
import math
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests

# Pure-Python TF-IDF — no scikit-learn required so the function fits inside
# Vercel's 250 MB unzipped serverless size cap.
_SKLEARN_AVAILABLE = True  # kept for backward-compat in match.py guard

# English stop words (subset matching scikit-learn's ENGLISH_STOP_WORDS that
# actually appear in Magic oracle text).
_STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing",
    "don", "down", "during", "each", "few", "for", "from", "further", "had",
    "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "s", "same", "she", "should", "so", "some", "such", "t", "than",
    "that", "the", "their", "theirs", "them", "themselves", "then", "there",
    "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
    "who", "whom", "why", "will", "with", "you", "your", "yours", "yourself",
    "yourselves",
})

_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]+\b")

# ---------- Constants & defaults ----------

MOXFIELD_USER_DECKS = (
    "https://api2.moxfield.com/v2/decks/search"
    "?authorUserNames={user}&pageNumber={page}&pageSize=100"
)
MOXFIELD_DECK = "https://api2.moxfield.com/v3/decks/all/{public_id}"

ARCHIDEKT_USER_DECKS = (
    "https://archidekt.com/api/decks/v3/"
    "?ownerUsername={user}&pageSize=100&page={page}"
)
ARCHIDEKT_DECK = "https://archidekt.com/api/decks/{deck_id}/"
# Archidekt format IDs we care about (1=Standard, 3=Commander/EDH, etc.).
# Reference: https://archidekt.com/forum/thread/40353/1
_ARCHIDEKT_COMMANDER_FORMAT = 3

SCRYFALL_NAMED_FUZZY = "https://api.scryfall.com/cards/named?fuzzy={name}"
SCRYFALL_NAMED_EXACT = "https://api.scryfall.com/cards/named?exact={name}"

# Local Scryfall bulk oracle index. Built by scripts/refresh_oracle.py.
# We ship the gz file with the repo so deployments don't need outbound network
# to look up the ~37K oracle cards.
_BULK_SLIM_PATH = Path(__file__).resolve().parent.parent / "data" / "oracle-slim.json.gz"
_BULK_META_PATH = Path(__file__).resolve().parent.parent / "data" / "oracle-meta.json"

HEADERS = {
    # A real-browser UA is required; the bare default is Cloudflare-blocked.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json",
}

CARD_TYPES = [
    "Creature", "Artifact", "Enchantment", "Planeswalker",
    "Instant", "Sorcery", "Battle", "Land",
]

DEFAULT_CONFIG: dict[str, Any] = {
    # Matching
    "fuzzy_match_threshold": 0.88,   # 0..1, difflib ratio for deck-side fuzzy direct hits
    "scryfall_fuzzy_lookup": True,   # use Scryfall's ?fuzzy= for purchase-name resolution
    "enforce_commander_legality": True,  # skip cards banned in Commander as upgrade candidates
    "include_commanders_in_legality_check": True,  # also reject color-illegal cards
    # Scoring — keyword mode
    "type_weight": 2.0,
    "theme_weight": 3.0,
    # Scoring — TF-IDF mode
    "tfidf_ngram_max": 2,         # use unigrams + bigrams
    "tfidf_min_df": 2,            # token must appear in >=2 cards in corpus to count
    "tfidf_max_df": 0.6,          # ignore tokens appearing in >60% of cards (boilerplate)
    "tfidf_type_bonus": 5.0,      # additive points if candidate type matches deck's type histogram
    # Hybrid mode blends scores: final = w_kw * keyword_score + w_tfidf * cosine*100
    "hybrid_keyword_weight": 0.4,
    "hybrid_tfidf_weight": 0.6,
    # Prerequisite check — penalize candidates whose payoff triggers refer to
    # types/subtypes/keywords that the target deck doesn't actually contain in
    # sufficient numbers. Set `prereq_enabled` false to disable entirely.
    "prereq_enabled": True,
    "prereq_min_satisfied": 6,   # need >= N matching cards in deck to be "satisfied"
    "prereq_soft_floor": 2,      # below this, penalty is at its harshest
    "prereq_min_multiplier": 0.15,  # multiplier applied when deck has 0 matches (harshest)
    "prereq_partial_multiplier": 0.55,  # multiplier when between floor and min_satisfied
    # Reporting
    "top_n": 5,
    # Theme keywords — scanned in lower-cased oracle text on both sides.
    # Edit/add freely. Each match contributes log2(1 + count) * theme_weight.
    "theme_keywords": [
        # strategies
        "draw a card", "draws a card", "create a treasure", "create a token",
        "extra combat", "additional combat", "attacks", "attacking",
        "sacrifice", "dies", "enters the battlefield", "etb",
        "counter target", "copy target", "exile target", "destroy target",
        "+1/+1 counter", "proliferate", "scry", "surveil", "mill",
        # keywords
        "lifelink", "deathtouch", "flying", "trample", "haste", "menace",
        "vigilance", "indestructible", "hexproof", "ward", "flash",
        # artifact / vehicle / equip themes
        "vehicle", "crew", "equipment", "equip", "aura", "enchant creature",
        "artifact you control", "artifacts you control",
        # tribal
        "soldier", "human", "wizard", "knight", "warrior", "samurai",
        "dragon", "demon", "angel", "spirit", "zombie", "vampire",
        # ramp / cost
        "mana of any color", "add {", "search your library for a", "land card",
        "reduce the cost", "costs {", "less to cast",
    ],
}


# ---------- Config loading ----------

def load_config(path: Path | None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path is None:
        return cfg
    if not path.exists():
        print(f"Config file not found: {path} (using defaults)", file=sys.stderr)
        return cfg
    try:
        user_cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Config file is not valid JSON ({e}); using defaults", file=sys.stderr)
        return cfg
    # Shallow merge at the top level — full overwrite for lists like theme_keywords
    for k, v in user_cfg.items():
        cfg[k] = v
    return cfg


# ---------- HTTP ----------

def get_json(url: str, retries: int = 3, sleep: float = 1.0) -> dict:
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                # Don't retry 404s
                raise RuntimeError(f"HTTP 404 at {url}")
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(sleep * (i + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def list_user_decks(user: str) -> list[dict]:
    decks: list[dict] = []
    page = 1
    while True:
        data = get_json(MOXFIELD_USER_DECKS.format(user=user, page=page))
        chunk = data.get("data", [])
        if not chunk:
            break
        decks.extend(chunk)
        if len(decks) >= data.get("totalResults", 0):
            break
        page += 1
    return decks


def fetch_deck(public_id: str) -> dict:
    return get_json(MOXFIELD_DECK.format(public_id=public_id))


# ---------- Archidekt -------------------------------------------------------

_ARCHIDEKT_COLOR_NAME_TO_LETTER = {
    "White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G",
}


def list_archidekt_decks(user: str) -> list[dict]:
    """List decks for an Archidekt username. Returns normalized stubs that
    look like the Moxfield ones (publicId, name, publicUrl, colorIdentity)."""
    decks: list[dict] = []
    page = 1
    while True:
        data = get_json(ARCHIDEKT_USER_DECKS.format(user=user, page=page))
        chunk = data.get("results") or []
        if not chunk:
            break
        for r in chunk:
            colors = r.get("colors") or []
            color_ids = sorted({
                _ARCHIDEKT_COLOR_NAME_TO_LETTER[c]
                for c in colors
                if c in _ARCHIDEKT_COLOR_NAME_TO_LETTER
            })
            decks.append({
                # Reuse "publicId" key so the rest of the pipeline doesn't
                # have to special-case the source.
                "publicId": str(r.get("id")),
                "name": r.get("name"),
                "publicUrl": f"https://archidekt.com/decks/{r.get('id')}",
                "colorIdentity": color_ids,
                "format": r.get("deckFormat"),
                "_source": "archidekt",
            })
        if not data.get("next"):
            break
        page += 1
        if page > 50:  # safety stop
            break
    return decks


def fetch_archidekt_deck(deck_id: str) -> dict:
    return get_json(ARCHIDEKT_DECK.format(deck_id=deck_id))


# ---------- Source dispatcher -----------------------------------------------

_VALID_SOURCES = ("moxfield", "archidekt")


def list_user_decks_for(user: str, source: str) -> list[dict]:
    if source == "moxfield":
        out = list_user_decks(user)
        for d in out:
            d.setdefault("_source", "moxfield")
        return out
    if source == "archidekt":
        return list_archidekt_decks(user)
    raise ValueError(f"Unknown deck source: {source!r}")


def fetch_deck_for(public_id: str, source: str) -> dict:
    if source == "moxfield":
        d = fetch_deck(public_id)
        d.setdefault("_source", "moxfield")
        return d
    if source == "archidekt":
        d = fetch_archidekt_deck(public_id)
        d.setdefault("_source", "archidekt")
        return d
    raise ValueError(f"Unknown deck source: {source!r}")


# Regex for parsing direct deck references (URLs or bare IDs).
_MOXFIELD_URL_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]{18,})", re.I)
_ARCHIDEKT_URL_RE = re.compile(r"archidekt\.com/(?:decks|deck)/(\d+)", re.I)
_MOXFIELD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{18,}$")
_ARCHIDEKT_ID_RE = re.compile(r"^\d{4,}$")


def parse_deck_references(text: str) -> list[tuple[str, str]]:
    """Parse a free-form blob into a list of (source, public_id) tuples.
    Accepts:
      - Full Moxfield URLs (https://moxfield.com/decks/<id>)
      - Full Archidekt URLs (https://archidekt.com/decks/<id>)
      - Bare Moxfield IDs (18+ base64url-like chars)
      - Bare Archidekt IDs (all-digits)
    Items can be separated by commas, semicolons, whitespace, or newlines.
    Duplicates are removed; order is preserved.
    """
    if not text:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    # First sweep: URL matches (they may be embedded in any string).
    for m in _MOXFIELD_URL_RE.finditer(text):
        key = ("moxfield", m.group(1))
        if key not in seen:
            seen.add(key); out.append(key)
    for m in _ARCHIDEKT_URL_RE.finditer(text):
        key = ("archidekt", m.group(1))
        if key not in seen:
            seen.add(key); out.append(key)
    # Second sweep: bare tokens that aren't part of any URL we already matched.
    residual = _MOXFIELD_URL_RE.sub(" ", text)
    residual = _ARCHIDEKT_URL_RE.sub(" ", residual)
    for raw in re.split(r"[\s,;]+", residual):
        tok = raw.strip()
        if not tok:
            continue
        if _ARCHIDEKT_ID_RE.match(tok):
            key = ("archidekt", tok)
        elif _MOXFIELD_ID_RE.match(tok):
            key = ("moxfield", tok)
        else:
            continue
        if key not in seen:
            seen.add(key); out.append(key)
    return out


# ---------- Card-name normalization ----------

def _normalize_token(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[\u2018\u2019']", "", name)
    name = re.sub(r"[^a-z0-9 ,.-]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def normalize_name(name: str) -> str:
    """Canonical key for the WHOLE card (front face only)."""
    return _normalize_token(name.split(" // ")[0])


def face_names(name: str) -> list[str]:
    """Normalized tokens for every face of a DFC/MDFC/split/adventure name."""
    return [_normalize_token(p) for p in name.split(" // ") if p.strip()]


def fuzzy_find_in_deck(
    target_faces: list[str],
    deck_face_index: dict[str, str],
    threshold: float,
) -> str | None:
    """
    Given normalized face tokens for the target AND a {face_norm: deck_card_norm}
    index, return the deck-card key whose ANY face matches the target's ANY face
    (exact, else fuzzy above threshold). Returns None if no match.
    """
    for tf in target_faces:
        if tf in deck_face_index:
            return deck_face_index[tf]
    if threshold >= 1.0:
        return None
    best_key = None
    best_score = 0.0
    for tf in target_faces:
        for face_norm, deck_key in deck_face_index.items():
            score = difflib.SequenceMatcher(None, tf, face_norm).ratio()
            if score > best_score:
                best_score = score
                best_key = deck_key
    return best_key if best_score >= threshold else None


# ---------- Card type / theme extraction ----------

def types_of(type_line: str) -> set[str]:
    if not type_line:
        return set()
    main = type_line.split("—")[0]
    return {t for t in CARD_TYPES if t.lower() in main.lower()}


def themes_of(oracle_text: str, keywords: list[str]) -> set[str]:
    if not oracle_text:
        return set()
    return {kw for kw in keywords if kw in oracle_text}


# ---------- Deck extraction ----------

def _merged_type_line(card: dict) -> str:
    faces = card.get("card_faces") or []
    if faces:
        return " // ".join((f.get("type_line") or "") for f in faces)
    return card.get("type_line") or card.get("type") or ""


def _merged_oracle(card: dict) -> str:
    faces = card.get("card_faces") or []
    if faces:
        return " ".join((f.get("oracle_text") or "") for f in faces).lower()
    return (card.get("oracle_text") or "").lower()


def extract_deck_cards(deck: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """
    Flatten mainboard + commanders + companions + signatureSpells into
    {norm_name: info}. Also return a face-name index {face_norm: norm_name}
    so MDFC back-face matches work for direct hits, and oracle text from
    all faces is merged for theme scoring.
    """
    out: dict[str, dict] = {}
    face_index: dict[str, str] = {}
    boards = deck.get("boards", {})
    for board_name in ("mainboard", "commanders", "companions", "signatureSpells"):
        board = boards.get(board_name, {}) or {}
        for entry in (board.get("cards") or {}).values():
            card = entry.get("card") or {}
            name = card.get("name")
            if not name:
                continue
            key = normalize_name(name)
            out[key] = {
                "name": name,
                "type_line": _merged_type_line(card),
                "color_identity": set(card.get("color_identity") or []),
                "oracle_text": _merged_oracle(card),
                "cmc": card.get("cmc"),
                "quantity": entry.get("quantity", 1),
                "board": board_name,
            }
            # Index every face (front, back, split halves, adventure) -> deck key
            for face in face_names(name):
                face_index.setdefault(face, key)
    return out, face_index


# Archidekt categories that mark a card as not-mainboard or non-deck.
_ARCHIDEKT_SKIP_CATEGORIES = {
    "sideboard", "maybeboard", "considering", "acquire",
    "do not include", "reserve",
}


def _archidekt_compose_type_line(oc: dict) -> str:
    """Compose a Scryfall-style 'Super Type — Subtype' string from
    Archidekt's oracleCard.{superTypes, types, subTypes} arrays."""
    faces = oc.get("faces") or []
    if faces and isinstance(faces[0], dict):
        # MDFC / split / adventure — join faces with ' // ' to match Scryfall.
        return " // ".join(
            _archidekt_compose_type_line(f) for f in faces
        )
    supers = [s for s in (oc.get("superTypes") or []) if s]
    types = [t for t in (oc.get("types") or []) if t]
    subs = [s for s in (oc.get("subTypes") or []) if s]
    left = " ".join(supers + types).strip()
    if subs:
        return f"{left} — {' '.join(subs)}".strip()
    return left


def _archidekt_compose_oracle(oc: dict) -> str:
    faces = oc.get("faces") or []
    if faces and isinstance(faces[0], dict):
        return " ".join((f.get("text") or "") for f in faces).lower()
    return (oc.get("text") or "").lower()


def _archidekt_color_identity(oc: dict) -> set[str]:
    """Archidekt uses full color names; map to Scryfall letters."""
    return {
        _ARCHIDEKT_COLOR_NAME_TO_LETTER[c]
        for c in (oc.get("colorIdentity") or [])
        if c in _ARCHIDEKT_COLOR_NAME_TO_LETTER
    }


def extract_archidekt_deck_cards(deck: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """Archidekt analog of extract_deck_cards. Archidekt uses a flat
    `cards` array; each entry has categories that label its board
    (mainboard, Sideboard, Maybeboard, Commander, etc.)."""
    out: dict[str, dict] = {}
    face_index: dict[str, str] = {}
    for entry in deck.get("cards") or []:
        cats = entry.get("categories") or []
        cats_lower = [str(c).strip().lower() for c in cats]
        if any(c in _ARCHIDEKT_SKIP_CATEGORIES for c in cats_lower):
            continue
        card = entry.get("card") or {}
        oc = card.get("oracleCard") or {}
        name = oc.get("name") or card.get("displayName")
        if not name:
            continue
        # Detect commander / companion / signatureSpells via category names.
        if "commander" in cats_lower:
            board = "commanders"
        elif "companion" in cats_lower:
            board = "companions"
        elif "signature spell" in cats_lower or "signaturespells" in cats_lower:
            board = "signatureSpells"
        else:
            board = "mainboard"
        key = normalize_name(name)
        out[key] = {
            "name": name,
            "type_line": _archidekt_compose_type_line(oc),
            "color_identity": _archidekt_color_identity(oc),
            "oracle_text": _archidekt_compose_oracle(oc),
            "cmc": oc.get("cmc"),
            "quantity": entry.get("quantity", 1),
            "board": board,
        }
        for face in face_names(name):
            face_index.setdefault(face, key)
    return out, face_index


def _archidekt_deck_color_identity(deck: dict) -> set[str]:
    ci: set[str] = set()
    for entry in deck.get("cards") or []:
        oc = (entry.get("card") or {}).get("oracleCard") or {}
        ci |= _archidekt_color_identity(oc)
    return ci


# ---------- Scryfall ----------

_scryfall_cache: dict[str, dict | None] = {}

# Lazy-loaded local bulk index. Two dicts to support both exact and
# face-name (split / MDFC / adventure / transform) lookups.
_bulk_index: dict[str, dict] | None = None      # normalized full name -> raw card
_bulk_face_index: dict[str, dict] | None = None  # normalized face name -> raw card
_bulk_meta: dict | None = None
_bulk_load_failed: bool = False


def _bulk_normalize(name: str) -> str:
    """Normalize a card name for index lookup.

    Lowercase, strip whitespace, collapse internal whitespace, and treat
    'A / B' / 'A/B' / 'A//B' as the canonical 'a // b' Scryfall form.
    """
    s = (name or "").lower().strip()
    # Normalize split-card separator variants to ' // '
    s = re.sub(r"\s*/{1,2}\s*", " // ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _load_bulk_index() -> tuple[dict[str, dict], dict[str, dict]]:
    """Load and index the slim Scryfall oracle bulk file (lazy + cached).

    Returns ({full_name -> card}, {face_name -> card}). If the bulk file is
    missing or unreadable, returns empty dicts and sets _bulk_load_failed so
    we fall straight through to the HTTP path on every lookup.
    """
    global _bulk_index, _bulk_face_index, _bulk_meta, _bulk_load_failed
    if _bulk_index is not None and _bulk_face_index is not None:
        return _bulk_index, _bulk_face_index
    if _bulk_load_failed:
        return {}, {}
    try:
        import gzip
        if not _BULK_SLIM_PATH.exists():
            print(f"  bulk oracle file not found at {_BULK_SLIM_PATH}; falling back to HTTP",
                  file=sys.stderr)
            _bulk_load_failed = True
            _bulk_index, _bulk_face_index = {}, {}
            return _bulk_index, _bulk_face_index
        t0 = time.time()
        with gzip.open(_BULK_SLIM_PATH, "rb") as f:
            cards = json.loads(f.read())
        full: dict[str, dict] = {}
        faces: dict[str, dict] = {}
        for c in cards:
            n = _bulk_normalize(c.get("name") or "")
            if n:
                full.setdefault(n, c)
            for face in (c.get("card_faces") or []):
                fn = _bulk_normalize(face.get("name") or "")
                if fn:
                    faces.setdefault(fn, c)
        _bulk_index, _bulk_face_index = full, faces
        if _BULK_META_PATH.exists():
            try:
                _bulk_meta = json.loads(_BULK_META_PATH.read_text())
            except Exception:
                _bulk_meta = None
        elapsed = time.time() - t0
        meta_age = (_bulk_meta or {}).get("updated_at", "unknown")
        print(f"  loaded bulk oracle index: {len(cards)} cards "
              f"({len(full)} full + {len(faces)} face keys) in {elapsed:.2f}s "
              f"[updated_at={meta_age}]",
              file=sys.stderr)
        return _bulk_index, _bulk_face_index
    except Exception as e:
        print(f"  bulk oracle load failed ({e}); falling back to HTTP",
              file=sys.stderr)
        _bulk_load_failed = True
        _bulk_index, _bulk_face_index = {}, {}
        return _bulk_index, _bulk_face_index


def _info_from_bulk(card: dict, input_name: str) -> dict:
    """Shape a slim bulk-index card into the dict shape lookup_scryfall returns."""
    name = card.get("name") or input_name
    return {
        "name": name,
        "type_line": _merged_type_line(card),
        "color_identity": set(card.get("color_identity") or []),
        "oracle_text": _merged_oracle(card),
        "cmc": card.get("cmc"),
        "legalities": card.get("legalities") or {},
        "input": input_name,
        "faces": face_names(name),
        "layout": card.get("layout"),
    }


def _lookup_bulk(name: str) -> dict | None:
    """Look up a card in the local bulk index. Returns the shaped info dict
    or None if the name isn't present."""
    full, faces = _load_bulk_index()
    if not full and not faces:
        return None
    key = _bulk_normalize(name)
    card = full.get(key) or faces.get(key)
    if card is None:
        # Last-ditch: 'A' alone matching 'a // b' (split half typed without partner).
        # Try matching the first face in any split-card name.
        if " // " not in key:
            for full_key, c in full.items():
                if " // " in full_key and full_key.split(" // ", 1)[0] == key:
                    card = c
                    break
        if card is None:
            return None
    return _info_from_bulk(card, name)


def _lookup_scryfall_http(name: str, fuzzy: bool = True) -> dict | None:
    """Hit the Scryfall HTTP named endpoint. Respects the 80ms politeness gap.
    Used only as a fallback when the bulk index misses (e.g. spoilers released
    after the last bulk refresh, or user typos that fuzzy can resolve)."""
    url = (SCRYFALL_NAMED_FUZZY if fuzzy else SCRYFALL_NAMED_EXACT).format(
        name=requests.utils.quote(name)
    )
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "card-matcher/2.0", "Accept": "application/json"},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return {
            "name": data.get("name"),
            "type_line": _merged_type_line(data),
            "color_identity": set(data.get("color_identity") or []),
            "oracle_text": _merged_oracle(data),
            "cmc": data.get("cmc"),
            "legalities": data.get("legalities") or {},
            "input": name,
            "faces": face_names(data.get("name") or name),
            "layout": data.get("layout"),
        }
    except requests.RequestException:
        return None
    finally:
        time.sleep(0.08)  # Scryfall asks ~80ms between requests


def lookup_scryfall(name: str, fuzzy: bool = True) -> dict | None:
    """Resolve a card by name.

    Order of operations:
      1. In-process cache.
      2. Local Scryfall bulk oracle index (no network).
      3. Scryfall HTTP named endpoint as a fallback (rate-limited).
    """
    cache_key = name.lower().strip()
    if cache_key in _scryfall_cache:
        return _scryfall_cache[cache_key]
    info = _lookup_bulk(name)
    if info is None:
        info = _lookup_scryfall_http(name, fuzzy=fuzzy)
    _scryfall_cache[cache_key] = info
    return info


# ---------- Purchase loading ----------

def parse_purchase_text(text: str) -> list[dict]:
    """Plain-text mode: one card per line, optional 'Nx' or 'N ' quantity prefix."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip trailing collector-set info like "(set) 123"
        line = re.sub(r"\s*\([^)]+\)\s*\d*\s*$", "", line).strip()
        m = re.match(r"^(\d+)\s*x?\s+(.*)$", line)
        if m:
            rows.append({"name": m.group(2).strip(), "quantity": m.group(1),
                         "price": "", "set": "", "notes": ""})
        else:
            rows.append({"name": line, "quantity": "1", "price": "", "set": "", "notes": ""})
    return rows


def parse_purchase_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        row_lc = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        name = row_lc.get("name") or row_lc.get("card") or row_lc.get("card name")
        if not name:
            continue
        rows.append({
            "name": name,
            "quantity": row_lc.get("quantity") or row_lc.get("qty") or "1",
            "price": row_lc.get("price") or "",
            "set": row_lc.get("set") or "",
            "notes": row_lc.get("notes") or "",
        })
    return rows


def load_purchases(text: str) -> list[dict]:
    """Auto-detect CSV vs plain text. CSV requires a header row containing 'name'/'card'."""
    first_line = next((l for l in text.splitlines() if l.strip()), "")
    looks_csv = "," in first_line and re.search(r"\b(name|card)\b", first_line, re.I)
    return parse_purchase_csv(text) if looks_csv else parse_purchase_text(text)


# ---------- Matching ----------

def color_legal(card_ci: set[str], deck_ci: set[str]) -> bool:
    return card_ci.issubset(deck_ci)


def commander_legal(info: dict) -> bool:
    """True if Scryfall says the card is legal (or restricted) in Commander."""
    leg = (info.get("legalities") or {}).get("commander", "").lower()
    return leg in ("legal", "restricted")


# ---------- Prerequisite check ----------
#
# Why this exists: text-pattern scorers (keyword + TF-IDF) match a candidate
# against deck oracle text, which can light up on incidental mentions. Sram,
# Senior Edificer triggers off casting Equipment or Aura spells — if a deck
# has zero Equipment and one Aura, recommending Sram is wrong even if the
# oracle text corpus mentions "equipment" or "aura" in a few cards.
#
# This module asks: what does the candidate CARE about, and is that ACTUALLY
# in the deck in usable quantity? If not, apply a multiplier to the score and
# surface the shortfall as a reason.

# Subtypes / tokens we look up in candidate text to build prerequisites.
# Each maps to a predicate over a deck card (info dict with type_line + oracle_text).
_SUBTYPE_PREREQS: list[tuple[re.Pattern, str, str]] = [
    # (pattern in candidate oracle text, prereq label, predicate key)
    (re.compile(r"\b(equipment|equip(?:ped)?)\b"), "Equipment cards", "subtype:equipment"),
    (re.compile(r"\baura(?:s)?\b"),               "Aura cards", "subtype:aura"),
    (re.compile(r"\benchant creature\b"),         "Aura cards", "subtype:aura"),
    (re.compile(r"\bvehicle(?:s)?\b"),             "Vehicles", "subtype:vehicle"),
    (re.compile(r"\bcrew\b"),                      "Vehicles", "subtype:vehicle"),
    (re.compile(r"\bsaga(?:s)?\b"),                 "Sagas", "subtype:saga"),
    (re.compile(r"\bgod(?:s)?\b"),                  "Gods", "subtype:god"),
    # tribal
    (re.compile(r"\bdragon(?:s)?\b"),               "Dragon creatures", "subtype:dragon"),
    (re.compile(r"\bangel(?:s)?\b"),                "Angel creatures", "subtype:angel"),
    (re.compile(r"\bdemon(?:s)?\b"),                "Demon creatures", "subtype:demon"),
    (re.compile(r"\bsoldier(?:s)?\b"),              "Soldier creatures", "subtype:soldier"),
    (re.compile(r"\bsamurai\b"),                    "Samurai creatures", "subtype:samurai"),
    (re.compile(r"\bknight(?:s)?\b"),               "Knight creatures", "subtype:knight"),
    (re.compile(r"\bwizard(?:s)?\b"),               "Wizard creatures", "subtype:wizard"),
    (re.compile(r"\bwarrior(?:s)?\b"),              "Warrior creatures", "subtype:warrior"),
    (re.compile(r"\bzombie(?:s)?\b"),               "Zombie creatures", "subtype:zombie"),
    (re.compile(r"\bvampire(?:s)?\b"),              "Vampire creatures", "subtype:vampire"),
    (re.compile(r"\bspirit(?:s)?\b"),               "Spirit creatures", "subtype:spirit"),
    (re.compile(r"\bhuman(?:s)?\b"),                "Human creatures", "subtype:human"),
    (re.compile(r"\belf\b|\belves\b"),              "Elf creatures", "subtype:elf"),
    (re.compile(r"\bgoblin(?:s)?\b"),               "Goblin creatures", "subtype:goblin"),
    (re.compile(r"\bmerfolk\b"),                    "Merfolk creatures", "subtype:merfolk"),
    (re.compile(r"\btreasure(?:s)?\b"),             "Treasure makers/cards", "subtype:treasure"),
    (re.compile(r"\bclue(?:s)?\b"),                 "Clue makers", "subtype:clue"),
    (re.compile(r"\bfood\b"),                       "Food makers", "subtype:food"),
]

# Card-type prerequisites: "whenever you cast an artifact spell" etc.
_TYPE_PREREQS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(?:cast|play|whenever you cast|whenever .* enters?)\b.{0,40}\b(artifact spell|artifact)s?\b"),
     "Artifact cards", "type:Artifact"),
    (re.compile(r"\b(?:cast|play|whenever you cast)\b.{0,40}\b(enchantment spell|enchantment)s?\b"),
     "Enchantment cards", "type:Enchantment"),
    (re.compile(r"\b(?:cast|play|whenever you cast)\b.{0,40}\b(instant|sorcery|noncreature spell|spell with mana value)\b"),
     "Instant/Sorcery spells", "type:InstantSorcery"),
    (re.compile(r"\bartifact you control\b|\bartifacts you control\b"),
     "Artifact cards", "type:Artifact"),
    (re.compile(r"\benchantment you control\b|\benchantments you control\b"),
     "Enchantment cards", "type:Enchantment"),
    (re.compile(r"\bcreature you control\b|\bcreatures you control\b"),
     "Creatures", "type:Creature"),
]

# Mechanical / keyword prerequisites: needs deck cards that DO this thing, not
# just mention it. "attacks" is matched broadly because almost every combat
# payoff cares about attackers being present — we count creatures.
_KW_PREREQS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\blandfall\b|whenever a land enters"),
     "Lands (>=35 typical)", "keyword:landfall"),
    (re.compile(r"whenever .* attacks?\b|attacks each combat"),
     "Attacking creatures", "keyword:attackers"),
    (re.compile(r"whenever .* deals combat damage"),
     "Combat-damage dealers", "keyword:combat_damage"),
    (re.compile(r"\bcopy\b.*\b(target spell|instant or sorcery)\b"),
     "Instants/Sorceries to copy", "keyword:copy_spell"),
    (re.compile(r"\bproliferate\b|\+1/\+1 counter on"),
     "Cards with +1/+1 counters / counters", "keyword:counters"),
    (re.compile(r"\bsacrifice a creature\b"),
     "Sac fodder creatures", "keyword:sac_fodder"),
    (re.compile(r"\bgraveyard\b.{0,40}\b(return|reanimate|cast from)"),
     "Reanimation targets in graveyard", "keyword:reanimate"),
]


def _deck_count_predicate(predicate_key: str, deck_cards: dict) -> int:
    """Count how many deck cards satisfy a given prerequisite predicate key."""
    if predicate_key.startswith("subtype:"):
        st = predicate_key.split(":", 1)[1]
        n = 0
        for c in deck_cards.values():
            tl = (c.get("type_line") or "").lower()
            # subtype must appear AFTER the em-dash (true subtype), not just in oracle text
            if "—" in tl:
                if st in tl.split("—", 1)[1]:
                    n += 1
            elif "-" in tl and (" - " in tl or tl.count("-") >= 1):
                # some sources use plain dash; be tolerant
                if st in tl:
                    n += 1
        return n
    if predicate_key.startswith("type:"):
        t = predicate_key.split(":", 1)[1]
        if t == "InstantSorcery":
            wanted = {"Instant", "Sorcery"}
            return sum(1 for c in deck_cards.values() if types_of(c["type_line"]) & wanted)
        return sum(1 for c in deck_cards.values() if t in types_of(c["type_line"]))
    if predicate_key == "keyword:landfall":
        return sum(1 for c in deck_cards.values() if "Land" in types_of(c["type_line"]))
    if predicate_key == "keyword:attackers":
        # creatures that can plausibly attack (any creature counts)
        return sum(1 for c in deck_cards.values() if "Creature" in types_of(c["type_line"]))
    if predicate_key == "keyword:combat_damage":
        return sum(1 for c in deck_cards.values() if "Creature" in types_of(c["type_line"]))
    if predicate_key == "keyword:copy_spell":
        wanted = {"Instant", "Sorcery"}
        return sum(1 for c in deck_cards.values() if types_of(c["type_line"]) & wanted)
    if predicate_key == "keyword:counters":
        return sum(
            1 for c in deck_cards.values()
            if "+1/+1 counter" in (c.get("oracle_text") or "") or "proliferate" in (c.get("oracle_text") or "")
        )
    if predicate_key == "keyword:sac_fodder":
        return sum(1 for c in deck_cards.values() if "Creature" in types_of(c["type_line"]))
    if predicate_key == "keyword:reanimate":
        return sum(1 for c in deck_cards.values() if "Creature" in types_of(c["type_line"]))
    return 0


def prerequisites_for(card: dict) -> list[tuple[str, str]]:
    """
    Inspect a candidate's oracle text and return a list of (label, predicate_key)
    prerequisites it needs the deck to satisfy.
    """
    text = (card.get("oracle_text") or "").lower()
    if not text:
        return []
    seen: set[str] = set()
    prereqs: list[tuple[str, str]] = []
    for pat, label, key in _SUBTYPE_PREREQS + _TYPE_PREREQS + _KW_PREREQS:
        if key in seen:
            continue
        if pat.search(text):
            seen.add(key)
            prereqs.append((label, key))
    return prereqs


def prerequisite_multiplier(
    card: dict,
    deck_cards: dict,
    cfg: dict,
) -> tuple[float, list[str]]:
    """
    Return (multiplier, reason_strings). multiplier in [min, 1.0].

    If a candidate has no prerequisites we can detect, returns (1.0, []).
    If ALL of its prereqs are satisfied, returns (1.0, []).
    Otherwise applies the harshest tier triggered by any unsatisfied prereq
    and lists each shortfall in the reasons (with actual counts).
    """
    if not cfg.get("prereq_enabled", True):
        return 1.0, []
    prereqs = prerequisites_for(card)
    if not prereqs:
        return 1.0, []

    min_sat = int(cfg["prereq_min_satisfied"])
    floor = int(cfg["prereq_soft_floor"])
    min_mult = float(cfg["prereq_min_multiplier"])
    partial_mult = float(cfg["prereq_partial_multiplier"])

    worst_mult = 1.0
    shortfall_msgs: list[str] = []
    for label, key in prereqs:
        n = _deck_count_predicate(key, deck_cards)
        if n >= min_sat:
            continue
        if n <= floor:
            mult = min_mult
            shortfall_msgs.append(f"deck has only {n} {label} (needs ≥{min_sat})")
        else:
            mult = partial_mult
            shortfall_msgs.append(f"deck has {n} {label} (light; ≥{min_sat} ideal)")
        if mult < worst_mult:
            worst_mult = mult
    return worst_mult, shortfall_msgs


# ---------- Replacement suggestions ----------
#
# Given an upgrade card and the target deck, find which existing deck cards
# are the safest to cut. We score every non-keeper deck card by:
#
#   replacement_score = similarity × cuttability
#
# where similarity = type overlap + shared theme categories + CMC closeness
# (+ small TF-IDF cosine if available), and cuttability rewards cards whose
# role is heavily duplicated elsewhere in the deck (redundant payoffs are
# safer to drop) and penalizes cards we never want to suggest cutting
# (commanders, lands, the candidate's literal duplicates).

# Keepers we will never suggest cutting.
_NEVER_CUT_TYPES = {"Land"}


def _is_commander_card(card: dict, deck: dict) -> bool:
    cmd = (deck.get("commander") or "").lower()
    if not cmd:
        return False
    # Commander field may be "A // B" for partners/backgrounds.
    parts = [p.strip() for p in cmd.replace("//", "/").split("/")]
    nm = (card.get("name") or "").lower()
    return any(nm == p for p in parts if p)


def _cmc_proximity(a: float, b: float) -> float:
    """Return a 0..1 score; 1.0 if same CMC, 0 if >=4 apart."""
    diff = abs(float(a) - float(b))
    if diff >= 4:
        return 0.0
    return 1.0 - (diff / 4.0)


def _role_redundancy(deck_card: dict, deck_cards: dict, theme_keywords: list[str]) -> int:
    """
    How many OTHER cards in the deck share at least one theme keyword with
    this card AND at least one card type? Used as the cuttability signal:
    a redundant payoff is safer to drop.
    """
    dc_themes = themes_of(deck_card.get("oracle_text") or "", theme_keywords)
    if not dc_themes:
        return 0
    dc_types = types_of(deck_card.get("type_line") or "")
    if not dc_types:
        return 0
    count = 0
    for k, other in deck_cards.items():
        if other is deck_card:
            continue
        if (other.get("name") or "").lower() == (deck_card.get("name") or "").lower():
            continue
        other_themes = themes_of(other.get("oracle_text") or "", theme_keywords)
        if not (dc_themes & other_themes):
            continue
        other_types = types_of(other.get("type_line") or "")
        if dc_types & other_types:
            count += 1
    return count


# --- Theme alignment / off-theme scoring -------------------------------------

# Cosine threshold above which the upgrade is considered "on-theme" with the deck.
# Deck profiles are noisy (removal/ramp staples dominate top terms), so we rely
# primarily on cosine — top-term overlap is a confirmation bonus, not a gate.
_THEME_ALIGN_COSINE_STRONG = 0.25  # cosine alone is enough at this level
_THEME_ALIGN_COSINE_THRESHOLD = 0.18  # need shared-top-term to qualify here
_THEME_ALIGN_TERM_OVERLAP_MIN = 1
_DECK_TOP_TERMS_N = 8


def _detect_alignment(
    upgrade_card: dict,
    deck: dict,
    tfidf: "TfidfScorer",
) -> tuple[bool, float, list[str]]:
    """
    Returns (is_aligned, cosine, shared_top_terms).

    The upgrade is 'aligned' with the deck's theme when (a) its TF-IDF cosine
    against the deck profile is above the alignment threshold AND (b) at
    least one of its strong terms is in the deck's top-N TF-IDF terms.
    """
    up_text = _clean_oracle(upgrade_card.get("oracle_text") or "")
    if not up_text:
        return False, 0.0, []
    up_vec = tfidf._vectorize(up_text)
    if not up_vec:
        return False, 0.0, []

    deck_idx = tfidf.deck_idx.get(id(deck))
    if deck_idx is None:
        return False, 0.0, []
    deck_vec = tfidf.deck_vectors[deck_idx]
    cos = TfidfScorer._cosine(up_vec, deck_vec)

    deck_top = {t for t, _ in tfidf.top_terms_for_deck(deck_idx, k=_DECK_TOP_TERMS_N)}
    # Strong upgrade terms = top 5 weighted features in its own vector.
    up_strong = sorted(up_vec.items(), key=lambda x: -x[1])[:5]
    up_strong_terms = {tfidf.idx_to_term[i] for i, _ in up_strong}
    shared = sorted(up_strong_terms & deck_top)

    # Two ways to qualify as aligned:
    #   1. Strong cosine alone (>= _THEME_ALIGN_COSINE_STRONG), OR
    #   2. Moderate cosine (>= _THEME_ALIGN_COSINE_THRESHOLD) AND at least one
    #      shared top-N term as confirmation.
    aligned = (
        cos >= _THEME_ALIGN_COSINE_STRONG
        or (
            cos >= _THEME_ALIGN_COSINE_THRESHOLD
            and len(shared) >= _THEME_ALIGN_TERM_OVERLAP_MIN
        )
    )
    return aligned, cos, shared


def _off_theme_score(
    deck_card: dict,
    deck: dict,
    tfidf: "TfidfScorer",
    deck_top_terms: set[str],
) -> tuple[float, dict]:
    """
    Compute an "off-theme" score for a deck card in [0, 1.5] range.

    Combines two signals:
      1. (1 - card_cosine_to_deck) — outliers vs the deck profile
      2. +0.5 if the card has NO overlap with the deck's top-N terms
    """
    dc_text = _clean_oracle(deck_card.get("oracle_text") or "")
    if not dc_text:
        # No text — likely vanilla creature. Treat as moderately off-theme.
        return 0.6, {"cosine": 0.0, "hits_top_term": False}
    dc_vec = tfidf._vectorize(dc_text)
    if not dc_vec:
        return 0.6, {"cosine": 0.0, "hits_top_term": False}

    deck_idx = tfidf.deck_idx[id(deck)]
    deck_vec = tfidf.deck_vectors[deck_idx]
    cos = TfidfScorer._cosine(dc_vec, deck_vec)

    dc_strong_terms = {
        tfidf.idx_to_term[i] for i, _ in
        sorted(dc_vec.items(), key=lambda x: -x[1])[:5]
    }
    hits_top = bool(dc_strong_terms & deck_top_terms)

    off_theme = (1.0 - cos) + (0.0 if hits_top else 0.5)
    return off_theme, {"cosine": cos, "hits_top_term": hits_top}


def find_replacements(
    upgrade_card: dict,
    deck: dict,
    cfg: dict,
    tfidf: "TfidfScorer | None" = None,
    top_k: int = 3,
    mode: str = "auto",
) -> dict:
    """
    Return up to `top_k` cards from `deck` that are the safest to cut in
    favor of `upgrade_card`, sorted best-first.

    `mode` controls the cuttability logic:
      - 'redundant': reward role-redundant cards (default for off-theme upgrades)
      - 'reinforce': reward off-theme cards (protect the deck's theme)
      - 'auto':      pick 'reinforce' if the upgrade aligns with the deck's
                      dominant TF-IDF theme, else 'redundant'

    Returns a dict: {
      mode: <effective mode actually used>,
      alignment: {cosine, shared_terms, aligned},
      candidates: [{name, score, ...}],
    }
    """
    keywords = cfg["theme_keywords"]
    up_types = types_of(upgrade_card.get("type_line") or "")
    up_themes = themes_of(upgrade_card.get("oracle_text") or "", keywords)
    up_cmc = float(upgrade_card.get("cmc") or 0.0)
    up_name_norm = normalize_name(upgrade_card.get("name") or "")
    up_text_clean = _clean_oracle(upgrade_card.get("oracle_text") or "")

    # Pre-compute TF-IDF vector for the upgrade card once, if available
    up_vec = tfidf._vectorize(up_text_clean) if (tfidf is not None and up_text_clean) else None

    # Resolve auto-mode and gather alignment metadata for the report.
    alignment = {"aligned": False, "cosine": 0.0, "shared_terms": []}
    effective_mode = mode
    if tfidf is not None:
        aligned, cos, shared = _detect_alignment(upgrade_card, deck, tfidf)
        alignment = {"aligned": aligned, "cosine": round(cos, 3), "shared_terms": shared}
        if mode == "auto":
            effective_mode = "reinforce" if aligned else "redundant"
    elif mode == "auto":
        # No TF-IDF available — can't auto-detect; default to redundant.
        effective_mode = "redundant"

    # Cache the deck's top terms for off-theme detection.
    deck_top_terms: set[str] = set()
    if tfidf is not None and effective_mode == "reinforce":
        deck_idx = tfidf.deck_idx[id(deck)]
        deck_top_terms = {t for t, _ in tfidf.top_terms_for_deck(deck_idx, k=_DECK_TOP_TERMS_N)}

    candidates: list[dict] = []
    for key, dc in deck["cards"].items():
        # --- Hard filters: keepers we never suggest cutting ---
        dc_types = types_of(dc.get("type_line") or "")
        if dc_types & _NEVER_CUT_TYPES:
            continue
        if _is_commander_card(dc, deck):
            continue
        # Don't suggest cutting a card with the same name as the upgrade.
        if normalize_name(dc.get("name") or "") == up_name_norm:
            continue
        # Need shared type for slot-equivalence; otherwise swap doesn't make
        # sense (e.g. cutting a sorcery to slot in a creature).
        if not (dc_types & up_types):
            continue

        # --- Similarity (same in both modes) ---
        type_overlap = len(dc_types & up_types)
        dc_themes = themes_of(dc.get("oracle_text") or "", keywords)
        shared_themes = up_themes & dc_themes
        theme_overlap = len(shared_themes)
        dc_cmc = float(dc.get("cmc") or 0.0)
        cmc_prox = _cmc_proximity(up_cmc, dc_cmc)

        sim_tfidf = 0.0
        if up_vec and tfidf is not None:
            dc_text = _clean_oracle(dc.get("oracle_text") or "")
            dc_vec = tfidf._vectorize(dc_text) if dc_text else None
            if dc_vec:
                sim_tfidf = TfidfScorer._cosine(up_vec, dc_vec)

        similarity = (
            1.0 * type_overlap
            + 2.0 * theme_overlap
            + 1.5 * cmc_prox
            + 4.0 * sim_tfidf
        )
        if similarity <= 0:
            continue

        # --- Cuttability (mode-dependent) ---
        redundancy = _role_redundancy(dc, deck["cards"], keywords)
        reasons: list[str] = []

        if effective_mode == "reinforce" and tfidf is not None:
            # Theme-build: within the same-type pool, prefer cutting the
            # least-thematic option. Same-role/redundant cards are protected
            # because they ARE the theme.
            off_theme, info = _off_theme_score(dc, deck, tfidf, deck_top_terms)
            # Range ~[0.5, 2.0]
            cuttability = 0.5 + 1.5 * min(1.0, off_theme)
            if redundancy >= 5:
                cuttability *= 0.7
            elif redundancy >= 2:
                cuttability *= 0.85
            if shared_themes:
                sample = list(shared_themes)[:3]
                reasons.append("same role: " + ", ".join(sample))
            reasons.append(
                f"off-theme: cosine {info['cosine']:.2f} vs deck"
                + ("" if info["hits_top_term"] else "; no top-term overlap")
            )
            if redundancy >= 5:
                reasons.append("protected: redundant role IS the theme")
        else:
            # Redundant-cut mode: reward role-redundant cards.
            cuttability = 1.0 + min(1.5, 0.4 * redundancy)
            if dc_cmc >= 5 and theme_overlap == 0:
                cuttability *= 0.9
            if dc_cmc >= 5 and theme_overlap >= 1 and redundancy >= 2:
                cuttability *= 1.15
            if shared_themes:
                sample = list(shared_themes)[:3]
                reasons.append("same role: " + ", ".join(sample))
            if redundancy >= 1:
                shown = f"{redundancy}" if redundancy < 10 else "10+"
                reasons.append(f"{shown} similar payoff(s) already in deck")

        if cmc_prox >= 0.75:
            reasons.append(f"close CMC ({dc_cmc:g} vs {up_cmc:g})")
        if sim_tfidf > 0.05 and effective_mode != "reinforce":
            reasons.append(f"text overlap {sim_tfidf:.2f}")

        final = similarity * cuttability

        candidates.append({
            "name": dc.get("name"),
            "type_line": dc.get("type_line"),
            "cmc": dc_cmc,
            "score": round(final, 2),
            "similarity": round(similarity, 2),
            "cuttability": round(cuttability, 2),
            "redundancy": redundancy,
            "shared_themes": sorted(shared_themes),
            "reasons": reasons,
        })

    candidates.sort(key=lambda c: -c["score"])
    return {
        "mode": effective_mode,
        "alignment": alignment,
        "candidates": candidates[:top_k],
    }


def score_upgrade_keyword(
    card: dict,
    deck_cards: dict,
    deck_ci: set[str],
    cfg: dict,
) -> tuple[float, list[str]]:
    """Original keyword-counting scorer."""
    if cfg["include_commanders_in_legality_check"] and not color_legal(card["color_identity"], deck_ci):
        return 0.0, []

    card_types = types_of(card["type_line"])
    card_themes = themes_of(card["oracle_text"], cfg["theme_keywords"])

    type_hits: dict[str, int] = defaultdict(int)
    theme_hits: dict[str, int] = defaultdict(int)
    for dc in deck_cards.values():
        for t in types_of(dc["type_line"]) & card_types:
            type_hits[t] += 1
        for kw in themes_of(dc["oracle_text"], cfg["theme_keywords"]) & card_themes:
            theme_hits[kw] += 1

    reasons = []
    if type_hits:
        top_types = sorted(type_hits.items(), key=lambda x: -x[1])[:3]
        reasons.append("type overlap: " + ", ".join(f"{t} ({n})" for t, n in top_types))
    if theme_hits:
        top_themes = sorted(theme_hits.items(), key=lambda x: -x[1])[:4]
        reasons.append("theme overlap: " + ", ".join(f"\u201c{k}\u201d ({n})" for k, n in top_themes))

    type_score = sum(math.log2(1 + n) for n in type_hits.values()) * cfg["type_weight"]
    theme_score = sum(math.log2(1 + n) for n in theme_hits.values()) * cfg["theme_weight"]
    return round(type_score + theme_score, 1), reasons


# ---------- TF-IDF scoring ----------

_BASIC_LANDS = {"plains", "island", "swamp", "mountain", "forest", "wastes",
                "snow-covered plains", "snow-covered island", "snow-covered swamp",
                "snow-covered mountain", "snow-covered forest"}

_ORACLE_CLEAN_RE = re.compile(r"[\{\}\(\)\[\]\u2014\u2022\"\u201c\u201d\u2018\u2019]")


def _clean_oracle(text: str) -> str:
    """Strip mana-symbol braces, reminder-text parens, and noisy punctuation."""
    if not text:
        return ""
    # Drop reminder text in parentheses
    text = re.sub(r"\([^)]*\)", " ", text)
    text = _ORACLE_CLEAN_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _deck_corpus_text(deck_cards: dict) -> str:
    """Concatenate oracle text of every non-basic-land card in the deck."""
    parts = []
    for c in deck_cards.values():
        if (c["name"] or "").lower() in _BASIC_LANDS:
            continue
        parts.append(_clean_oracle(c["oracle_text"]))
    return " ".join(parts)


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, drop stop-words and single-letter tokens."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP_WORDS]


def _make_ngrams(tokens: list[str], n_max: int) -> list[str]:
    out: list[str] = list(tokens)
    for n in range(2, n_max + 1):
        out.extend(" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1))
    return out


class TfidfScorer:
    """
    Pure-Python TF-IDF.

    Builds one document per deck (concatenated card oracle text) using a shared
    vocabulary fit on card-level docs, so IDF reflects card-level rarity — rare
    phrases like "landfall" or "myriad" get high weight, common phrases like
    "target creature" get low weight. Scores a candidate by cosine similarity
    between its cleaned oracle text and the deck profile.

    Closely mirrors `sklearn.feature_extraction.text.TfidfVectorizer(
        ngram_range=(1, ngram_max), min_df=N, max_df=F, stop_words='english',
        sublinear_tf=True, norm='l2', smooth_idf=True)`.
    """

    def __init__(self, decks: list[dict], cfg: dict):
        self.cfg = cfg
        ngram_max = int(cfg["tfidf_ngram_max"])
        min_df = int(cfg["tfidf_min_df"])
        max_df = float(cfg["tfidf_max_df"])

        # --- Card-level corpus for vocabulary + IDF ---
        card_token_docs: list[list[str]] = []
        for d in decks:
            for c in d["cards"].values():
                if (c["name"] or "").lower() in _BASIC_LANDS:
                    continue
                txt = _clean_oracle(c["oracle_text"])
                if not txt:
                    continue
                card_token_docs.append(_make_ngrams(_tokenize(txt), ngram_max))

        if len(card_token_docs) < 10:
            raise RuntimeError(
                f"Too few cards ({len(card_token_docs)}) to build a meaningful TF-IDF corpus."
            )

        n_cards = len(card_token_docs)
        df: dict[str, int] = defaultdict(int)
        for doc in card_token_docs:
            for term in set(doc):
                df[term] += 1

        max_df_count = int(max_df * n_cards) if max_df <= 1.0 else int(max_df)
        vocab = {
            term: idx for idx, term in enumerate(
                sorted(t for t, c in df.items() if c >= min_df and c <= max_df_count)
            )
        }
        if not vocab:
            raise RuntimeError("TF-IDF vocabulary is empty after min_df/max_df filtering.")

        # smooth_idf=True: idf = ln((1+n)/(1+df)) + 1
        self.vocab = vocab
        self.idx_to_term = {i: t for t, i in vocab.items()}
        self.idf = {
            term: math.log((1 + n_cards) / (1 + df[term])) + 1.0
            for term in vocab
        }
        self.ngram_max = ngram_max

        # --- Deck-level vectors ---
        self.decks = decks
        self.deck_idx = {id(d): i for i, d in enumerate(decks)}
        self.deck_vectors: list[dict[int, float]] = []
        for d in decks:
            self.deck_vectors.append(self._vectorize(_deck_corpus_text(d["cards"])))

        # Pre-compute deck type histograms for the type bonus.
        self.deck_type_share: list[dict[str, float]] = []
        for d in decks:
            total = max(1, len(d["cards"]))
            share: dict[str, float] = {}
            for t in CARD_TYPES:
                n = sum(1 for c in d["cards"].values() if t in types_of(c["type_line"]))
                share[t] = n / total
            self.deck_type_share.append(share)

    def _vectorize(self, text: str) -> dict[int, float]:
        """Return a sparse {feature_idx: l2-normalized tfidf_weight} dict."""
        if not text:
            return {}
        terms = _make_ngrams(_tokenize(text), self.ngram_max)
        if not terms:
            return {}
        # raw term frequencies, in-vocab only
        tf: dict[int, int] = defaultdict(int)
        for t in terms:
            idx = self.vocab.get(t)
            if idx is not None:
                tf[idx] += 1
        if not tf:
            return {}
        # sublinear_tf: 1 + log(tf)
        vec: dict[int, float] = {}
        for idx, count in tf.items():
            term = self.idx_to_term[idx]
            vec[idx] = (1.0 + math.log(count)) * self.idf[term]
        # l2 normalize
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec

    @staticmethod
    def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
        if not a or not b:
            return 0.0
        # vectors are already l2-normalized → cosine == dot product
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(k, 0.0) for k, v in a.items())

    def top_terms_for_deck(self, deck_idx: int, k: int = 6) -> list[tuple[str, float]]:
        vec = self.deck_vectors[deck_idx]
        if not vec:
            return []
        items = sorted(vec.items(), key=lambda x: -x[1])[:k]
        return [(self.idx_to_term[i], float(v)) for i, v in items if v > 0]

    def score(self, card: dict, deck: dict) -> tuple[float, list[str]]:
        if self.cfg["include_commanders_in_legality_check"] and not color_legal(
            card["color_identity"], deck["color_identity"]
        ):
            return 0.0, []

        card_text = _clean_oracle(card["oracle_text"])
        if not card_text:
            return 0.0, []

        card_vec = self._vectorize(card_text)
        deck_idx = self.deck_idx[id(deck)]
        deck_vec = self.deck_vectors[deck_idx]
        cos = self._cosine(card_vec, deck_vec)

        # Identify top shared weighted features for the reasons line.
        reasons: list[str] = []
        if card_vec and deck_vec:
            contrib: list[tuple[int, float]] = []
            for idx, v in card_vec.items():
                dv = deck_vec.get(idx, 0.0)
                if dv > 0:
                    contrib.append((idx, v * dv))
            contrib.sort(key=lambda x: -x[1])
            top_terms = [self.idx_to_term[i] for i, c in contrib[:5] if c > 0]
            if top_terms:
                reasons.append("shared terms: " + ", ".join(f"\u201c{t}\u201d" for t in top_terms))

        # Type bonus: small additive if candidate is a type that's dense in the deck
        type_bonus = 0.0
        for t in types_of(card["type_line"]):
            type_bonus += self.deck_type_share[deck_idx].get(t, 0.0)
        type_bonus *= self.cfg["tfidf_type_bonus"]

        score = cos * 100.0 + type_bonus
        reasons.append(f"cosine {cos:.3f}; type-fit +{type_bonus:.1f}")
        return round(score, 1), reasons


def score_upgrade(
    card: dict,
    deck: dict,
    cfg: dict,
    mode: str,
    tfidf: "TfidfScorer | None",
) -> tuple[float, list[str]]:
    """Dispatch to keyword/tfidf/hybrid scoring, then apply prerequisite check."""
    if mode == "keyword":
        base, reasons = score_upgrade_keyword(card, deck["cards"], deck["color_identity"], cfg)
    elif mode == "tfidf":
        base, reasons = tfidf.score(card, deck)
    elif mode == "hybrid":
        kw_score, kw_reasons = score_upgrade_keyword(card, deck["cards"], deck["color_identity"], cfg)
        tf_score, tf_reasons = tfidf.score(card, deck)
        # Both scorers gate on color; if either returned 0 from gating, treat as 0.
        if kw_score <= 0.0 and tf_score <= 0.0:
            return 0.0, []
        base = (
            cfg["hybrid_keyword_weight"] * kw_score
            + cfg["hybrid_tfidf_weight"] * tf_score
        )
        reasons = kw_reasons + tf_reasons
    else:
        raise ValueError(f"unknown scoring mode: {mode}")

    if base <= 0.0:
        return round(base, 1), reasons

    # Prerequisite gate: penalize candidates whose triggers reference types/
    # subtypes/keywords that aren't actually present in this deck in usable
    # quantity. This is where the tool justifies WHY a fit fails despite
    # matching text patterns.
    mult, prereq_reasons = prerequisite_multiplier(card, deck["cards"], cfg)
    adjusted = base * mult
    if mult < 1.0:
        reasons.append(
            f"prereq penalty ×{mult:.2f}: " + "; ".join(prereq_reasons)
        )
    return round(adjusted, 1), reasons


# ---------- Report ----------

def build_report(
    user: str,
    purchases: list[dict],
    deck_data: list[dict],
    out_path: Path,
    cfg: dict,
    scoring_mode: str = "keyword",
    replacement_mode: str = "auto",
) -> None:
    top_n = cfg["top_n"]
    fuzzy_threshold = cfg["fuzzy_match_threshold"]

    decks = []
    for d in deck_data:
        source = d.get("_source") or "moxfield"
        if source == "archidekt":
            cards, face_index = extract_archidekt_deck_cards(d)
            # Archidekt deck object doesn't always carry colorIdentity at the
            # top level when fetched by id; derive it from the cards.
            deck_ci = _archidekt_deck_color_identity(d)
            commander_names = [
                ((entry.get("card") or {}).get("oracleCard") or {}).get("name", "")
                for entry in (d.get("cards") or [])
                if any(str(c).strip().lower() == "commander"
                       for c in (entry.get("categories") or []))
            ]
            commander_str = ", ".join(n for n in commander_names if n) or "—"
            url = f"https://archidekt.com/decks/{d.get('id')}"
            public_id = str(d.get("id") or "")
        else:
            cards, face_index = extract_deck_cards(d)
            deck_ci = set(d.get("colorIdentity") or [])
            commander_str = ", ".join(
                (c.get("card") or {}).get("name", "")
                for c in (d.get("boards", {}).get("commanders", {}).get("cards") or {}).values()
            ) or "—"
            url = d.get("publicUrl") or f"https://moxfield.com/decks/{d.get('publicId')}"
            public_id = d.get("publicId") or ""
        decks.append({
            "name": d.get("name"),
            "public_id": public_id,
            "url": url,
            "color_identity": deck_ci,
            "cards": cards,
            "face_index": face_index,
            "commander": commander_str,
            "source": source,
        })

    # Build TF-IDF scorer if needed
    tfidf: TfidfScorer | None = None
    if scoring_mode in ("tfidf", "hybrid"):
        tfidf = TfidfScorer(decks, cfg)

    lines: list[str] = []
    # Header source label: derive from the actual decks; if mixed, say "deck sources".
    sources_used = sorted({d.get("source", "moxfield") for d in decks})
    if not sources_used:
        src_label = "Moxfield user"
    elif len(sources_used) == 1:
        src_label = {"moxfield": "Moxfield user", "archidekt": "Archidekt user"}.get(
            sources_used[0], f"{sources_used[0]} user"
        )
    else:
        src_label = "user (" + " + ".join(sources_used) + ")"
    lines.append(f"# Card Matching Report — {src_label} `{user}`")
    lines.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M %Z')}_  ")
    lines.append(f"_{len(purchases)} purchases · {len(decks)} decks · scoring: **{scoring_mode}** · fuzzy threshold {fuzzy_threshold} · Commander legality enforced: {cfg['enforce_commander_legality']}_\n")

    lines.append("## Decks analyzed\n")
    if tfidf is not None:
        lines.append("| Deck | Commander | Colors | Top TF-IDF terms |")
        lines.append("|---|---|---|---|")
        for i, d in enumerate(decks):
            ci = "".join(c for c in "WUBRG" if c in d["color_identity"]) or "Colorless"
            top_terms = tfidf.top_terms_for_deck(i, k=6)
            terms_str = ", ".join(f"_{t}_" for t, _ in top_terms) if top_terms else "—"
            lines.append(f"| [{d['name']}]({d['url']}) | {d['commander']} | {ci} | {terms_str} |")
    else:
        lines.append("| Deck | Commander | Colors |")
        lines.append("|---|---|---|")
        for d in decks:
            ci = "".join(c for c in "WUBRG" if c in d["color_identity"]) or "Colorless"
            lines.append(f"| [{d['name']}]({d['url']}) | {d['commander']} | {ci} |")
    lines.append("")

    direct_hit_summary: dict[str, list[str]] = defaultdict(list)
    upgrade_summary: dict[str, list[tuple[str, float, list[dict]]]] = defaultdict(list)
    unresolved: list[str] = []
    banned: list[str] = []

    lines.append("## Per-card analysis\n")
    for p in purchases:
        raw_name = p["name"]
        info = lookup_scryfall(raw_name, fuzzy=cfg["scryfall_fuzzy_lookup"])
        if not info:
            unresolved.append(raw_name)
            lines.append(f"### {raw_name}")
            lines.append("_Could not resolve on Scryfall — check spelling._\n")
            continue

        norm = normalize_name(info["name"])
        target_faces = info.get("faces") or [norm]
        is_legal = commander_legal(info)
        type_line = info["type_line"]
        ci_str = "".join(c for c in "WUBRG" if c in info["color_identity"]) or "Colorless"
        qty = p.get("quantity") or "1"
        price = p.get("price")
        notes = p.get("notes")

        header = f"### {info['name']}"
        if normalize_name(raw_name) != norm:
            header += f"  _(resolved from \u201c{raw_name}\u201d)_"
        header += "  \n"
        meta = f"_{type_line} · {ci_str} · CMC {info['cmc']}_"
        if qty and qty != "1":
            meta += f" · qty {qty}"
        if price:
            meta += f" · ${price}"
        if notes:
            meta += f" · {notes}"
        if cfg["enforce_commander_legality"] and not is_legal:
            meta += "  \n**\u26a0\ufe0f Banned in Commander** — flagged from upgrade candidates."
            banned.append(info["name"])
        lines.append(header + meta + "\n")

        # ----- Direct hits (with fuzzy fallback + MDFC face matching) -----
        hits = []
        for d in decks:
            match_key = fuzzy_find_in_deck(target_faces, d["face_index"], fuzzy_threshold)
            if match_key is None:
                continue
            matched = d["cards"][match_key]
            is_fuzzy = match_key != norm
            hits.append((d, matched, is_fuzzy))

        if hits:
            lines.append("**Already in:**")
            for d, matched, is_fuzzy in hits:
                board = matched["board"]
                tag = "" if board == "mainboard" else f" _(in {board})_"
                if is_fuzzy:
                    tag += f" _(fuzzy match: \u201c{matched['name']}\u201d)_"
                lines.append(f"- [{d['name']}]({d['url']}){tag}")
                direct_hit_summary[d["name"]].append(info["name"])
            lines.append("")

        # ----- Upgrade candidates -----
        if cfg["enforce_commander_legality"] and not is_legal:
            # Don't suggest banned cards as upgrades.
            continue

        scored = []
        hit_decks = {id(d) for d, _, _ in hits}
        for d in decks:
            if id(d) in hit_decks:
                continue
            score, reasons = score_upgrade(info, d, cfg, scoring_mode, tfidf)
            if score > 0.0 and reasons:
                scored.append((score, d, reasons))
        scored.sort(key=lambda x: -x[0])

        if scored:
            lines.append("**Fits well in:**")
            for score, d, reasons in scored[:top_n]:
                # Replacements are computed here so the Summary section can
                # display them, but they're NOT rendered inline per-card.
                rep = find_replacements(info, d, cfg, tfidf, top_k=3, mode=replacement_mode)
                lines.append(
                    f"- [{d['name']}]({d['url']}) \u2014 score {score}; "
                    + "; ".join(reasons)
                )
                upgrade_summary[d["name"]].append((info["name"], score, rep))
            lines.append("")
        elif not hits:
            legal_decks = [d["name"] for d in decks if color_legal(info["color_identity"], d["color_identity"])]
            if not legal_decks:
                lines.append("_Not color-legal in any of your decks._\n")
            else:
                lines.append(f"_Color-legal in {len(legal_decks)} deck(s) but no type/theme overlap found._\n")

    # ----- Summary by deck -----
    lines.append("\n## Summary by deck\n")
    for d in decks:
        nm = d["name"]
        dh = direct_hit_summary.get(nm, [])
        up = upgrade_summary.get(nm, [])
        if not dh and not up:
            continue
        lines.append(f"### [{nm}]({d['url']})")
        if dh:
            lines.append(f"**Already owns from purchase list ({len(dh)}):** " + ", ".join(sorted(set(dh))))
        if up:
            up_sorted = sorted(up, key=lambda x: -x[1])
            lines.append(f"**Upgrade candidates ({len(up_sorted)}):**")
            for name, score, rep in up_sorted:
                lines.append(f"- {name} (score {score})")
                cands = rep["candidates"] if isinstance(rep, dict) else []
                if cands:
                    mode = rep["mode"]
                    alignment = rep.get("alignment", {})
                    if mode == "reinforce":
                        summary_label = (
                            "safe cuts — reinforce theme "
                            f"(cosine {alignment.get('cosine', 0):.2f}"
                            + (
                                f"; shares “{', '.join(alignment.get('shared_terms', [])[:3])}”"
                                if alignment.get("shared_terms") else ""
                            ) + ")"
                        )
                    else:
                        summary_label = "safe cuts — reduce redundancy"
                    lines.append(f"  <details><summary>{summary_label}</summary>\n")
                    for r in cands:
                        rline = f"  - **{r['name']}** _(CMC {r['cmc']:g})_ \u2014 score {r['score']}"
                        if r["reasons"]:
                            rline += "; " + "; ".join(r["reasons"])
                        lines.append(rline)
                    lines.append("\n  </details>")
        lines.append("")

    # Footnotes
    if unresolved or banned:
        lines.append("## Notes\n")
        if unresolved:
            lines.append(f"**Unresolved on Scryfall ({len(unresolved)}):** " + ", ".join(unresolved))
        if banned:
            lines.append(f"**Banned in Commander ({len(banned)}):** " + ", ".join(banned))

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------- Purchase-input gathering ----------

def gather_purchases(args: argparse.Namespace) -> list[dict]:
    if args.purchases:
        return load_purchases(args.purchases.read_text(encoding="utf-8-sig"))
    if args.paste:
        if sys.stdin.isatty():
            print(
                "Paste your card list (one per line, optional \"2x\" prefix).",
                "Finish with Ctrl-D (Unix) or Ctrl-Z then Enter (Windows).",
                sep="\n",
                file=sys.stderr,
            )
        text = sys.stdin.read()
        if not text.strip():
            print("No input received on stdin.", file=sys.stderr)
            sys.exit(2)
        return load_purchases(text)
    print("Provide --purchases <file> or --paste (stdin).", file=sys.stderr)
    sys.exit(2)


# ---------- CLI ----------

def filter_decks(decks: list[dict], patterns: list[str]) -> list[dict]:
    """
    Keep decks whose name matches ANY pattern. Each pattern is matched as:
      - case-insensitive substring, OR
      - fnmatch glob (supports * and ?), OR
      - exact case-insensitive match.
    """
    if not patterns:
        return decks
    pats_lc = [p.lower() for p in patterns]
    kept = []
    for d in decks:
        nm = (d.get("name") or "").lower()
        if any(p in nm or fnmatch.fnmatchcase(nm, p) for p in pats_lc):
            kept.append(d)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--user", default=None,
                    help="Username on the chosen deck source(s). Optional if --extra-decks is used.")
    ap.add_argument("--source", choices=["moxfield", "archidekt", "both"], default="moxfield",
                    help="Deck source. 'both' queries Moxfield AND Archidekt for the same username "
                         "(useful only if your handle matches on both).")
    ap.add_argument("--archidekt-user", default=None,
                    help="Override the Archidekt username when --source is archidekt or both. "
                         "Defaults to --user if not set.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--purchases", type=Path, help="CSV or text file of purchased cards")
    src.add_argument("--paste", action="store_true",
                     help="Read cards from stdin (one per line, optional 'Nx' prefix)")
    ap.add_argument("--config", type=Path, default=None, help="Optional JSON config file")
    ap.add_argument("--scoring", choices=["keyword", "tfidf", "hybrid"], default="keyword",
                    help="How to score upgrade candidates. 'keyword' is the curated-list approach. "
                         "'tfidf' builds a per-deck profile vector and ranks by cosine similarity. "
                         "'hybrid' blends both (weights in config).")
    ap.add_argument("--decks", nargs="+", default=None,
                    help="Filter to decks whose name matches any pattern (substring or fnmatch glob, case-insensitive). "
                         "Example: --decks 'paper' 'counterint++'")
    ap.add_argument("--decks-file", type=Path, default=None,
                    help="File with one deck-name pattern per line (combined with --decks)")
    ap.add_argument("--out", default=Path("report.md"), type=Path, help="Output markdown path")
    ap.add_argument("--deck-cache", default=Path("decks_cache.json"), type=Path,
                    help="Cache fetched deck JSON to avoid refetch")
    ap.add_argument("--refresh", action="store_true", help="Ignore cache and refetch all decks")
    ap.add_argument("--replacement-mode", choices=["auto", "reinforce", "redundant"], default="auto",
                    help="Replacement suggestion mode. 'auto' detects theme alignment per upgrade; "
                         "'reinforce' always targets off-theme cuts to deepen the visible theme; "
                         "'redundant' always targets redundant role overlap (default behavior).")
    ap.add_argument("--extra-decks", nargs="+", default=None,
                    help="Direct Moxfield/Archidekt deck URLs or IDs to include in addition to (or instead of) the username search. "
                         "Useful when a public deck is missing from Moxfield's search index. "
                         "Examples: https://moxfield.com/decks/NNcqOt7UlUWCeo6F0FidlQ, https://archidekt.com/decks/12345678, NNcqOt7UlUWCeo6F0FidlQ")
    args = ap.parse_args()
    extra_refs: list[tuple[str, str]] = []
    if args.extra_decks:
        extra_refs = parse_deck_references(" ".join(args.extra_decks))
    if not args.user and not extra_refs:
        ap.error("Either --user or --extra-decks must be provided")

    cfg = load_config(args.config)
    purchases = gather_purchases(args)
    print(f"Loaded {len(purchases)} purchase entries", file=sys.stderr)

    sources = ["moxfield", "archidekt"] if args.source == "both" else [args.source]
    archidekt_user = args.archidekt_user or args.user
    user_decks: list[dict] = []
    if args.user:
        for s in sources:
            u = archidekt_user if s == "archidekt" else args.user
            print(f"Fetching {s} deck list for {u}...", file=sys.stderr)
            try:
                chunk = list_user_decks_for(u, s)
            except RuntimeError as e:
                print(f"  {s} fetch failed: {e}", file=sys.stderr)
                continue
            # Archidekt mixes formats; restrict to Commander when source is archidekt
            # (format ID 3). Moxfield search is already author-scoped.
            if s == "archidekt":
                chunk = [d for d in chunk if d.get("format") == _ARCHIDEKT_COMMANDER_FORMAT]
            print(f"  {s}: found {len(chunk)} decks", file=sys.stderr)
            user_decks.extend(chunk)
    # Stub entries for direct refs; they get fetched below alongside user_decks.
    have_keys = {f"{(d.get('_source') or 'moxfield')}:{d.get('publicId')}" for d in user_decks}
    for src, pid in extra_refs:
        key = f"{src}:{pid}"
        if key in have_keys:
            continue
        have_keys.add(key)
        user_decks.append({"publicId": pid, "name": f"[direct] {pid}", "_source": src})
        print(f"  direct: added {src} deck {pid}", file=sys.stderr)

    # ---- Deck filtering ----
    patterns: list[str] = list(args.decks or [])
    if args.decks_file:
        if args.decks_file.exists():
            patterns.extend(
                l.strip() for l in args.decks_file.read_text().splitlines()
                if l.strip() and not l.strip().startswith("#")
            )
        else:
            print(f"--decks-file not found: {args.decks_file}", file=sys.stderr)
            return 2
    if patterns:
        before = len(user_decks)
        user_decks = filter_decks(user_decks, patterns)
        print(f"  filtered to {len(user_decks)} of {before} decks (patterns: {patterns})",
              file=sys.stderr)
        if not user_decks:
            print("No decks matched the filter patterns.", file=sys.stderr)
            return 2

    cache = {}
    if args.deck_cache.exists() and not args.refresh:
        try:
            cache = json.loads(args.deck_cache.read_text())
        except json.JSONDecodeError:
            cache = {}

    full_decks = []
    for d in user_decks:
        pid = d.get("publicId")
        src = d.get("_source") or "moxfield"
        if not pid:
            continue
        cache_key = f"{src}:{pid}"
        if cache_key in cache and not args.refresh:
            cached = cache[cache_key]
            cached.setdefault("_source", src)
            full_decks.append(cached)
        else:
            print(f"  fetching [{src}] {d.get('name')}...", file=sys.stderr)
            try:
                deck = fetch_deck_for(pid, src)
                cache[cache_key] = deck
                full_decks.append(deck)
                time.sleep(0.4)
            except RuntimeError as e:
                print(f"    skipped: {e}", file=sys.stderr)

    args.deck_cache.write_text(json.dumps(cache))
    if args.scoring in ("tfidf", "hybrid") and not _SKLEARN_AVAILABLE:
        print("scikit-learn not installed. Run: pip install scikit-learn", file=sys.stderr)
        return 2
    report_user = args.user or "(direct decks)"
    build_report(report_user, purchases, full_decks, args.out, cfg, scoring_mode=args.scoring,
                 replacement_mode=args.replacement_mode)
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
