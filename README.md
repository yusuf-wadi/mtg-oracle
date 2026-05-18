# mtg-oracle

Match a Moxfield purchase list against your decks, with Commander-legal filtering, fuzzy name matching, and **prerequisite-aware scoring** so a card like *Sram, Senior Edificer* doesn't get ranked highly in a deck that has zero Equipment, one Aura, and zero Vehicles.

Three scoring modes:

- **keyword** — curated theme keywords + card-type overlap, log-dampened
- **tfidf** — per-deck TF-IDF profile of all card oracle text, cosine similarity vs candidate
- **hybrid** — weighted blend (default 60% tfidf / 40% keyword)

A prerequisite check then inspects each candidate's oracle text for trigger references (Equipment, Aura, Vehicle, Dragon, Artifact, Landfall, attackers, etc.) and verifies the target deck actually contains enough of those cards. If not, the score is multiplied down and the shortfall is surfaced as a reason in the report.

## Run locally

```bash
pip install -r requirements.txt
python lib/card_matcher.py --user waedi --paste --scoring hybrid \
  --config config.example.json --out report.md < cards.txt
```

See [MATCHER.md](./MATCHER.md) for the full CLI / config reference.

## Deploy to Vercel

```
vercel
```

This repo includes a Vercel Python serverless function at [`api/match.py`](api/match.py) and a vanilla HTML/JS GUI at [`public/index.html`](public/index.html). No build step.

### How the web app works

1. `POST /api/match` accepts `{ user, paste, scoring, decks? }` JSON.
2. The function fetches the user's deck list from Moxfield, filters by the deck patterns if any, fetches up to 8 deck details (Vercel's 60s execution limit), runs the matcher in the requested mode, and returns the markdown report.
3. The static frontend renders the markdown.

### Limits

Vercel Hobby caps function execution at 60s. The first ~6 deck fetches plus scoring takes 10–25s for typical Commander rosters. If you have many decks, use the deck filter input (comma-separated substrings or fnmatch globs) to narrow the run.

## Project layout

```
api/match.py             Vercel Python serverless function
lib/card_matcher.py      Matching engine (importable)
lib/__init__.py
public/index.html        GUI
public/app.js
public/style.css
config.example.json      Theme keywords + scoring weights
data/oracle-slim.json.gz Scryfall oracle bulk index (~3 MB gz, refreshed monthly)
data/oracle-meta.json    Bulk-data manifest (updated_at, sha256, card count)
scripts/refresh_oracle.py  Fetches & slims the latest oracle bulk file
.github/workflows/refresh-oracle.yml  Monthly + manual-dispatch refresh
vercel.json
requirements.txt
MATCHER.md               Engine docs (CLI, config, scoring details)
```

## Scryfall oracle data

Card lookups (type lines, oracle text, color identity, legalities) come from a
local copy of Scryfall's [`oracle_cards` bulk file](https://scryfall.com/docs/api/bulk-data),
slimmed to the fields the matcher needs. The slim file is ~3 MB gzipped
(~37K cards) and ships in the deployment, so per-card HTTP requests and the
associated rate limits are avoided.

Lookup order:

1. In-process cache
2. Local bulk index (`data/oracle-slim.json.gz`)
3. Scryfall HTTP `/cards/named` (fallback for spoilers post-refresh or typos
   that need fuzzy resolution; still respects the 80 ms politeness gap)

**Refresh the bulk data:**

```
python scripts/refresh_oracle.py          # no-op if remote unchanged
python scripts/refresh_oracle.py --force  # always re-download
```

The GitHub Actions workflow runs the same script at 09:00 UTC on the 1st of
each month and commits the updated artifact. Trigger it manually after a set
release via the **Actions → Refresh Scryfall oracle data → Run workflow** button.

## License

MIT
