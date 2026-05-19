"""Shared deck-source resolution used by /api/match, /api/radar, /api/playability_decks.

Takes the dashboard's standard payload (user, source, archidekt_user, decks, extra_decks)
and returns a list of fully-hydrated deck objects from Moxfield/Archidekt.

This is a thin extraction from api/match.py's _run_match so radar + playability can
reuse the exact same fetching, filtering, dedup, and soft-cap logic.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import card_matcher as cm


def resolve_decks(payload: dict) -> tuple[list[dict], dict]:
    """Resolve (full_decks, meta) from a dashboard payload.

    On success: returns (decks, meta_dict).
    On user-input error: raises ValueError with a human-readable message.
    Fetch errors for individual decks are collected in meta['fetch_errors']
    rather than raised.
    """
    user = (payload.get("user") or "").strip()
    extra_decks_raw = payload.get("extra_decks") or ""
    extra_refs = cm.parse_deck_references(extra_decks_raw)
    if not user and not extra_refs:
        raise ValueError("Provide a username or at least one direct deck URL/ID.")

    source = (payload.get("source") or "moxfield").strip().lower()
    if source not in ("moxfield", "archidekt", "both"):
        raise ValueError(f"Invalid source: {source!r}")
    archidekt_user = (payload.get("archidekt_user") or "").strip() or user

    deck_patterns = payload.get("decks") or []
    if isinstance(deck_patterns, str):
        deck_patterns = [p.strip() for p in deck_patterns.split(",") if p.strip()]

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
            if s == "archidekt":
                chunk = [d for d in chunk if d.get("format") == cm._ARCHIDEKT_COMMANDER_FORMAT]
            user_decks.extend(chunk)

    if deck_patterns:
        user_decks = cm.filter_decks(user_decks, deck_patterns)

    have_keys = {(d.get("_source") or "moxfield", d.get("publicId")) for d in user_decks}

    MAX_DECKS = int(os.environ.get("MTG_ORACLE_MAX_DECKS", "8"))
    if len(user_decks) > MAX_DECKS:
        user_decks = user_decks[:MAX_DECKS]
        have_keys = {(d.get("_source") or "moxfield", d.get("publicId")) for d in user_decks}

    if not user_decks and not extra_refs:
        detail = ("; ".join(source_errors)) if source_errors else ""
        srcs = " + ".join(sources)
        msg = f"{srcs} user '{user}' has no public decks (or doesn't exist)."
        if detail:
            msg += f" [{detail}]"
        raise ValueError(msg)

    if deck_patterns and not user_decks and not extra_refs:
        raise ValueError("No decks matched the provided deck filter.")

    full_decks: list[dict] = []
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
        msg = "Could not fetch any deck details."
        if detail:
            msg += f" [{detail}]"
        raise ValueError(msg)

    meta = {
        "source": source,
        "sources_tried": sources,
        "user": user,
        "archidekt_user": archidekt_user,
        "extra_refs": extra_refs,
        "source_errors": source_errors,
        "fetch_errors": fetch_errors,
    }
    return full_decks, meta


def deck_display_name(deck: dict) -> str:
    """Best-effort human-readable name for a fetched deck."""
    return (
        deck.get("name")
        or deck.get("title")
        or deck.get("publicId")
        or "Untitled deck"
    )


def deck_color_identity(deck: dict) -> str:
    """Resolve the color identity letters for a deck (W/U/B/R/G/C)."""
    if deck.get("_source") == "archidekt":
        ci = cm._archidekt_deck_color_identity(deck)
    else:
        cards, _ = cm.extract_deck_cards(deck)
        ci: set[str] = set()
        for entry in cards.values():
            for c in (entry.get("color_identity") or []):
                ci.add(c)
    return "".join(sorted(ci)) if ci else "C"


def deck_decklist_text(deck: dict) -> str:
    """Render a deck as plain '<n> <name>' lines for the playability simulator."""
    if deck.get("_source") == "archidekt":
        cards, _ = cm.extract_archidekt_deck_cards(deck)
    else:
        cards, _ = cm.extract_deck_cards(deck)
    lines = []
    for entry in cards.values():
        qty = int(entry.get("quantity") or 1)
        name = entry.get("name")
        if not name:
            continue
        lines.append(f"{qty} {name}")
    return "\n".join(lines)


def deck_oracle_corpus(deck: dict) -> list[dict]:
    """Return [{name, type_line, oracle_text, quantity}, ...] for the deck.

    Used by the radar to score axis alignment per card and aggregate per deck.
    Both Moxfield and Archidekt extractors normalize to the same shape.
    """
    if deck.get("_source") == "archidekt":
        cards, _ = cm.extract_archidekt_deck_cards(deck)
    else:
        cards, _ = cm.extract_deck_cards(deck)
    out: list[dict] = []
    for entry in cards.values():
        out.append({
            "name": entry.get("name", ""),
            "type_line": entry.get("type_line", "") or "",
            "oracle_text": entry.get("oracle_text", "") or "",
            "quantity": int(entry.get("quantity") or 1),
        })
    return out
