# card_matcher — Moxfield purchase-list matcher

Pulls all public decks for a Moxfield user, then for each card on your purchase list reports:

1. **Direct hits** — decks where the card already appears (fuzzy-matched, so typos/diacritics still match).
2. **Upgrade candidates** — decks where the card is color-legal AND Commander-legal (banlist enforced) AND shares card types / theme keywords with cards already in the deck. Ranked by a log-dampened score so a tightly-themed deck beats one that just runs a lot of artifacts.

## Install

```bash
pip install requests
```

Python 3.10+.

## Usage

```bash
# File input
python card_matcher.py --user waedi --purchases purchases.csv --out report.md

# Paste interactively (one card per line, Ctrl-D to finish)
python card_matcher.py --user waedi --paste

# Pipe from stdin
cat my_list.txt | python card_matcher.py --user waedi --paste

# With a custom config
python card_matcher.py --user waedi --paste --config config.example.json
```

Flags:
- `--user` — Moxfield username (required)
- `--purchases <file>` — CSV or text file
- `--paste` — read cards from stdin instead of a file
- `--config <file>` — JSON config (optional; see below)
- `--out` — output markdown path (default `report.md`)
- `--deck-cache` — JSON cache for deck data (default `decks_cache.json`); reused across runs
- `--refresh` — ignore cache and re-fetch decks

`--purchases` and `--paste` are mutually exclusive; provide one.

## Purchase list formats

**CSV** (only `name` is mandatory):
```csv
name,quantity,price,set,notes
Smothering Tithe,1,29.99,RNA,for white decks
Rhystic Study,1,42.00,,blue staple
```
Aliases accepted: `name` / `card` / `card name`, `quantity` / `qty`.

**Plain text or paste** — one card per line, optional `2x` or `2 ` prefix, optional trailing set info `(RNA) 36`:
```
2x Lightning Bolt
Rhystic Study
Dockside Extortionist (CMR) 178
```

## Config file (JSON, optional)

Every field is optional and overrides the built-in default. Lists fully overwrite (they don't merge). See `config.example.json`.

| Key | Default | Purpose |
|---|---|---|
| `fuzzy_match_threshold` | `0.88` | difflib ratio threshold for deck-side fuzzy direct hits. Lower = more lenient. |
| `scryfall_fuzzy_lookup` | `true` | Use Scryfall's `?fuzzy=` endpoint for purchase-name resolution (forgives typos). |
| `enforce_commander_legality` | `true` | Skip Commander-banned cards from upgrade suggestions; flag them in the report. |
| `include_commanders_in_legality_check` | `true` | Require card's color identity ⊆ deck's color identity. |
| `type_weight` | `2.0` | Multiplier on shared card types. |
| `theme_weight` | `3.0` | Multiplier on shared theme keywords (the stronger signal). |
| `top_n` | `5` | How many top decks to list per card. |
| `theme_keywords` | ~50 defaults | Substrings scanned in lower-cased oracle text on both sides. |
| `prereq_enabled` | `true` | Penalize candidates whose triggers reference types/subtypes/keywords the deck doesn't actually contain. |
| `prereq_min_satisfied` | `6` | Need ≥ N matching cards in deck for a prereq to count as satisfied. |
| `prereq_soft_floor` | `2` | At or below this count, apply the harshest penalty. |
| `prereq_min_multiplier` | `0.15` | Score multiplier when deck has 0–2 matching cards. |
| `prereq_partial_multiplier` | `0.55` | Score multiplier when deck has 3–5 matching cards. |

## How matching works

- **Purchase-name resolution**: Scryfall's fuzzy `named` endpoint catches typos (`Rystic Study` → Rhystic Study).
- **Direct hit (already-owned)**: normalized name compared first; if no exact match, falls back to a difflib ratio against every card in the deck (catches diacritics and minor punctuation drift).
- **Color legality**: card's `color_identity` ⊆ deck's `color_identity`. Colorless artifacts fit everywhere; a U card stays out of mono-R `burn`.
- **Commander legality**: cards with Scryfall `legalities.commander = "banned"` are excluded from upgrades and listed under Notes. Affects Dockside Extortionist, Mana Crypt, Jeweled Lotus, Nadu, Channel, Black Lotus, etc.
- **Upgrade score**: `type_weight · Σ log₂(1 + type_matches) + theme_weight · Σ log₂(1 + theme_matches)`. The log keeps a 50-artifact deck from auto-winning every artifact suggestion; theme matches drive ranking because they capture specific synergies.
- **Prerequisite check**: after the base score, the tool inspects the candidate's oracle text for triggers referencing types/subtypes/keywords (Equipment, Aura, Vehicle, Dragon, Artifact, Landfall, Attacking creatures, etc.) and counts how many cards in the target deck actually satisfy each one. Below the floor, the score is multiplied by `prereq_min_multiplier`; partially short, by `prereq_partial_multiplier`. The shortfall and counts are surfaced as a reason line. This catches cases like Sram → Isshin: keyword overlap finds “equipment”/“aura” in oracle text, but the deck has 0 Equipment, 1 Aura, 0 Vehicles, so Sram is correctly penalized.

## Output

A markdown report:
1. **Decks analyzed** — table of decks, commanders, colors.
2. **Per-card analysis** — for each card: resolved name (with original if fuzzy), banned warning if applicable, decks it's already in (fuzzy-matched name shown if different), top upgrade fits with reasons.
3. **Summary by deck** — pivoted view: what each deck already owns from the list and the upgrade candidates ranked.
4. **Notes** — unresolved names and banned cards detected.

## Notes / limitations

- Moxfield's Cloudflare requires a real browser User-Agent — already baked in.
- Scryfall is rate-limited at ~80ms per request (their guideline); 20 purchases ≈ 2s.
- Theme tagging is text-substring, not semantic. Unique effects not in `theme_keywords` won't get a theme score (but still get type overlap). Add what matters to your meta via the config.
- The tool ignores sideboards/maybeboards/tokens for direct-hit detection; it considers mainboard + commanders + companions + signature spells.
- Banlist freshness depends on Scryfall — they update within ~24h of RC announcements.
