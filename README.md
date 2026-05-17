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
api/match.py            Vercel Python serverless function
lib/card_matcher.py     Matching engine (importable)
lib/__init__.py
public/index.html       GUI
public/app.js
public/style.css
config.example.json     Theme keywords + scoring weights
vercel.json
requirements.txt
MATCHER.md              Engine docs (CLI, config, scoring details)
```

## License

MIT
