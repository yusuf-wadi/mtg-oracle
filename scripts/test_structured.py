"""Smoke-test: call build_report on a tiny synthetic deck + purchases and
print a slice of the structured data. Doesn't hit any network."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import card_matcher as cm

# Minimal Moxfield-shaped deck with 3 cards.
DECK = {
    "_source": "moxfield",
    "name": "Test Attack Deck",
    "publicId": "TEST123",
    "publicUrl": "https://moxfield.com/decks/TEST123",
    "colorIdentity": ["R", "W"],
    "boards": {
        "mainboard": {"cards": {
            "a": {"quantity": 1, "card": {
                "name": "Aggravated Assault",
                "type_line": "Enchantment",
                "color_identity": ["R"],
                "oracle_text": "Pay {3}{R}: Untap all creatures you control. After the main phase this turn, there is an additional combat phase followed by an additional main phase. Activate only during your turn.",
                "cmc": 5.0,
                "legalities": {"commander": "legal"},
                "oracle_id": "x1",
            }},
            "b": {"quantity": 1, "card": {
                "name": "Lightning Greaves",
                "type_line": "Artifact \u2014 Equipment",
                "color_identity": [],
                "oracle_text": "Equipped creature has haste and shroud. Equip {0}",
                "cmc": 2.0,
                "legalities": {"commander": "legal"},
                "oracle_id": "x2",
            }},
            "c": {"quantity": 1, "card": {
                "name": "Sol Ring",
                "type_line": "Artifact",
                "color_identity": [],
                "oracle_text": "{T}: Add {C}{C}",
                "cmc": 1.0,
                "legalities": {"commander": "legal"},
                "oracle_id": "x3",
            }},
        }},
        "commanders": {"cards": {}},
    },
}

PURCHASES = cm.load_purchases("1x Aggravated Assault\n1x Smothering Tithe\n")

# Force the bulk index OFF so we don't need data files; pre-seed the scryfall cache.
cm._scryfall_cache["aggravated assault"] = {
    "name": "Aggravated Assault",
    "oracle_id": "x1",
    "type_line": "Enchantment",
    "color_identity": {"R"},
    "oracle_text": "Pay {3}{R}: Untap all creatures you control. After the main phase this turn, there is an additional combat phase followed by an additional main phase.",
    "cmc": 5.0,
    "legalities": {"commander": "legal"},
    "input": "Aggravated Assault",
    "faces": ["aggravated assault"],
    "layout": "normal",
    "tags": set(),
}
cm._scryfall_cache["smothering tithe"] = {
    "name": "Smothering Tithe",
    "oracle_id": "y1",
    "type_line": "Enchantment",
    "color_identity": {"W"},
    "oracle_text": "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token.",
    "cmc": 4.0,
    "legalities": {"commander": "legal"},
    "input": "Smothering Tithe",
    "faces": ["smothering tithe"],
    "layout": "normal",
    "tags": set(),
}

cfg = cm.load_config(None)
with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as tf:
    out_path = Path(tf.name)
data = cm.build_report("testuser", PURCHASES, [DECK], out_path, cfg, scoring_mode="keyword")
print("== STRUCTURED DATA ==")
print("decks:", len(data["decks"]))
for d in data["decks"]:
    print(f"  - {d['name']} ({d['public_id']}) ci={d['color_identity']} total_cards={d['total_cards']}")
    print(f"    family_scores top-3: {sorted(d['family_scores'].items(), key=lambda x: -x[1])[:3]}")
print()
print("candidates:", len(data["candidates"]))
for c in data["candidates"]:
    print(f"  - {c['name']} axes_hit={c['axis_count']} fits={len(c['fits'])} already_in={c['already_in']}")
    if c["axis_scores"]:
        print(f"    axes: {sorted(c['axis_scores'].keys())[:10]}")
    for f in c["fits"]:
        print(f"      fit -> deck={f['deck_public_id']} score={f['score']}")
