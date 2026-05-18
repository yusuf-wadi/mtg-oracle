#!/usr/bin/env python3
"""Capture before/after state for tag-integration A/B testing.

Runs a fixed purchase list against the Isshin deck and records
alignment + replacement candidates per upgrade. Can dump a JSON
snapshot or diff two snapshots.

Usage:
    python scripts/_baseline_test.py before.json
    # ...edit code...
    python scripts/_baseline_test.py after.json
    python scripts/_baseline_test.py --diff before.json after.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import card_matcher as cm  # noqa: E402

ISSHIN_PUBLIC_ID = "NNcqOt7UlUWCeo6F0FidlQ"

PURCHASE_TEXT = """\
1x Hellkite Charger
1x Aggravated Assault
1x Smothering Tithe
1x Akiri, Fearless Voyager
1x Cyclonic Rift
1x Bear Umbra
1x Sword of Feast and Famine
"""


def extract_summary() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "config.example.json"
    cfg = cm.load_config(cfg_path if cfg_path.exists() else None)

    deck = cm.fetch_deck("NNcqOt7UlUWCeo6F0FidlQ")
    # extract_deck_cards returns (cards, name_aliases); attach to deck dict
    cards, _aliases = cm.extract_deck_cards(deck)
    deck["cards"] = cards
    decks = [deck]

    tfidf = cm.TfidfScorer(decks, cfg)
    purchases = cm.load_purchases(PURCHASE_TEXT)

    summary = {"deck": deck.get("name") or "Isshin", "upgrades": []}
    for p in purchases:
        name = p.get("name") or ""
        info = cm.lookup_scryfall(name)
        if not info:
            summary["upgrades"].append({"name": name, "error": "lookup failed"})
            continue
        up_card = {
            "name": info.get("name") or name,
            "type_line": info.get("type_line") or "",
            "oracle_text": info.get("oracle_text") or "",
            "cmc": info.get("cmc") or 0.0,
            "color_identity": info.get("color_identity") or [],
            "oracle_id": info.get("oracle_id"),
            "tags": info.get("tags") or set(),
        }
        aligned, cos, shared = cm._detect_alignment(up_card, deck, tfidf)
        replacements = cm.find_replacements(
            up_card, deck, cfg, tfidf=tfidf, top_k=3, mode="auto"
        )
        summary["upgrades"].append({
            "name": up_card["name"],
            "tags": sorted(up_card["tags"])[:12],
            "alignment": {
                "aligned": aligned,
                "cosine": round(cos, 3),
                "shared_terms": shared,
            },
            "effective_mode": replacements.get("mode"),
            "candidates": [
                {"name": c.get("name"), "score": round(c.get("score", 0.0), 3)}
                for c in (replacements.get("candidates") or [])[:3]
            ],
        })
    return summary


def diff(before: dict, after: dict) -> str:
    lines = []
    bu = {u["name"]: u for u in before.get("upgrades", [])}
    au = {u["name"]: u for u in after.get("upgrades", [])}
    for up in sorted(set(bu) | set(au)):
        b = bu.get(up, {})
        a = au.get(up, {})
        balign = b.get("alignment", {}) or {}
        aalign = a.get("alignment", {}) or {}
        bmode = b.get("effective_mode", "?")
        amode = a.get("effective_mode", "?")
        bcand = [c["name"] for c in b.get("candidates", [])]
        acand = [c["name"] for c in a.get("candidates", [])]
        changed = (
            balign.get("aligned") != aalign.get("aligned")
            or bmode != amode
            or bcand != acand
        )
        if not changed:
            lines.append(
                f"  - {up}: (no change) aligned={aalign.get('aligned')} "
                f"mode={amode} cuts={acand[:1]}..."
            )
            continue
        lines.append(f"  * {up}:")
        lines.append(
            f"      aligned: {balign.get('aligned')} -> {aalign.get('aligned')}  "
            f"(cosine {balign.get('cosine')} -> {aalign.get('cosine')})"
        )
        lines.append(f"      mode:    {bmode} -> {amode}")
        lines.append(f"      cuts:    {bcand}")
        lines.append(f"           -> {acand}")
        if a.get("tags"):
            lines.append(f"      tags:    {a['tags'][:8]}")
    return "\n".join(lines)


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--diff":
        before = json.loads(Path(args[1]).read_text())
        after = json.loads(Path(args[2]).read_text())
        print(diff(before, after))
        return 0
    out_path = Path(args[0]) if args else Path("baseline.json")
    summary = extract_summary()
    out_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out_path}")
    print(f"Deck: {summary.get('deck')}: {len(summary.get('upgrades', []))} upgrades")
    return 0


if __name__ == "__main__":
    sys.exit(main())
