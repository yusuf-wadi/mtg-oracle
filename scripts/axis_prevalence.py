"""Compute per-axis hit rate across the whole oracle corpus.

The point of this script: an axis that fires on >25% of all cards in
Magic is almost certainly grammatical filler, not a deck theme. A real
theme like "treasure" fires on a few percent of cards at most. Use this
to identify and prune noisy axes.
"""
import gzip
import json
from collections import Counter
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.radar import _COMPILED  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "oracle-slim.json.gz"

with gzip.open(DATA, "rt", encoding="utf-8") as f:
    cards = json.load(f)

hits = Counter()
total = len(cards)

for c in cards:
    text = (c.get("oracle_text") or "")
    typeline = (c.get("type_line") or "")
    for axis_id, pats in _COMPILED.items():
        for is_typeline, pat in pats:
            target = typeline if is_typeline else text
            if pat.search(target):
                hits[axis_id] += 1
                break  # one hit per card per axis

# Sort axes by hit rate descending
rows = [(axis, n, n / total) for axis, n in hits.items()]
rows.sort(key=lambda r: -r[2])

print(f"Corpus size: {total:,} cards\n")
print(f"{'axis':<28} {'hits':>8} {'rate':>7}")
print("-" * 50)
for axis, n, rate in rows:
    marker = ""
    if rate >= 0.25:
        marker = "  <-- LIKELY NOISE"
    elif rate >= 0.15:
        marker = "  <-- watch"
    print(f"{axis:<28} {n:>8,} {rate * 100:>6.2f}% {marker}")
