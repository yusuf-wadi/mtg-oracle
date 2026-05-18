#!/usr/bin/env python3
"""
Refresh the local Scryfall oracle-tag index used by card_matcher.

Scryfall's official bulk-data feed does NOT include oracle tags (the
user-curated taxonomy at tagger.scryfall.com — e.g. otag:ramp,
otag:removal-spot, otag:tutor). We fetch them via the undocumented
GraphQL endpoint at https://tagger.scryfall.com/graphql, paginate every
tag's taggings, invert to oracle_id -> [tag_slug, ...], and write:

  data/oracle-tags.json.gz       gzipped JSON of the index (see SHAPE below)
  data/oracle-tags-meta.json     fetch metadata (counts, timestamp, sha256)

SHAPE of the slim oracle-tags artifact:
  {
    "tags":   ["40k-model", "ablative-armor", ..., "zubera"],   # sorted slugs
    "cards":  { "<oracle_id>": [tag_idx, tag_idx, ...] }        # idx into "tags"
  }
Storing tag_idx instead of slugs keeps the artifact small.

The full fetch is slow (~60-100 minutes) because there are ~4,400 tags
and ~220K card-tag edges and the endpoint rate-limits aggressively. Run
this in CI (monthly) or locally with --resume to recover from a partial
run.

Usage:
    python scripts/refresh_oracle_tags.py            # full refresh
    python scripts/refresh_oracle_tags.py --resume   # resume from progress file
    python scripts/refresh_oracle_tags.py --tag-limit 200   # debug: only the
                                                            # top 200 tags by
                                                            # tagging count
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor

TAGGER_HOST = "https://tagger.scryfall.com"
GRAPHQL_URL = f"{TAGGER_HOST}/graphql"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) card-matcher/2.0")

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "oracle-tags.json.gz"
META_PATH = DATA_DIR / "oracle-tags-meta.json"
PROGRESS_PATH = DATA_DIR / "_oracle-tags-progress.json"

# Politeness / rate-limit knobs. The tagger endpoint enforces a real limit; we
# saw 429s at >~3 req/sec sustained. 0.35s/request keeps us safely under that.
REQUEST_DELAY_S = 0.35
MAX_RETRIES = 5
RETRY_BASE_S = 5.0

# Tagger paginates taggings at a fixed perPage. Confirmed by inspection.
TAGGINGS_PER_PAGE = 75

SEARCH_TAGS_QUERY = """
query SearchTags($input: TagSearchInput!) {
  tags(input: $input) {
    page
    perPage
    total
    results {
      slug
      taggingCount
      namespace
      category
    }
  }
}
"""

# We fetch with descendants=false so each card-tag edge is counted once.
# (descendants=true rolls children into parents, inflating totals.)
FETCH_TAG_QUERY = """
query FetchTag($slug: String!, $page: Int!) {
  tag: tagBySlug(type: ORACLE_CARD_TAG, slug: $slug, aliasing: true) {
    slug
    taggings(page: $page, descendants: false) {
      page
      perPage
      total
      results {
        card { oracleId }
      }
    }
  }
}
"""


class TaggerClient:
    def __init__(self) -> None:
        self.opener = build_opener(HTTPCookieProcessor())
        self.csrf: str | None = None
        self._bootstrap()

    def _bootstrap(self) -> None:
        req = Request(f"{TAGGER_HOST}/", headers={"User-Agent": USER_AGENT})
        html = self.opener.open(req, timeout=30).read().decode("utf-8", "replace")
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if not m:
            raise RuntimeError("tagger.scryfall.com: csrf-token meta not found")
        self.csrf = m.group(1)
        print(f"  tagger client bootstrapped (csrf len={len(self.csrf)})",
              file=sys.stderr)

    def gql(self, query: str, variables: dict, op_name: str | None = None) -> dict:
        payload: dict = {"query": query, "variables": variables}
        if op_name:
            payload["operationName"] = op_name
        body = json.dumps(payload).encode()
        for attempt in range(1, MAX_RETRIES + 1):
            req = Request(
                GRAPHQL_URL, data=body, method="POST",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-CSRF-Token": self.csrf or "",
                    "Origin": TAGGER_HOST,
                    "Referer": f"{TAGGER_HOST}/",
                },
            )
            try:
                resp = self.opener.open(req, timeout=60)
                data = json.loads(resp.read())
                if data.get("errors"):
                    msg = data["errors"][0].get("message", "")
                    # "record not found" is expected for stale slugs — bubble up.
                    raise RuntimeError(f"graphql error: {msg}")
                return data["data"]
            except HTTPError as e:
                if e.code in (401, 403):
                    # CSRF token likely expired — re-bootstrap and retry.
                    print(f"  HTTP {e.code} — refreshing csrf and retrying",
                          file=sys.stderr)
                    self._bootstrap()
                    continue
                if e.code in (429, 502, 503, 504):
                    wait = RETRY_BASE_S * (2 ** (attempt - 1))
                    print(f"  HTTP {e.code} — backing off {wait:.0f}s "
                          f"(attempt {attempt}/{MAX_RETRIES})", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise
            except (URLError, TimeoutError) as e:
                wait = RETRY_BASE_S * (2 ** (attempt - 1))
                print(f"  network error: {e} — retrying in {wait:.0f}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
        raise RuntimeError("graphql: max retries exceeded")


def list_oracle_tags(client: TaggerClient) -> list[dict]:
    """Enumerate every ORACLE_CARD_TAG via paginated SearchTags."""
    out: list[dict] = []
    page = 1
    while True:
        data = client.gql(SEARCH_TAGS_QUERY,
                          {"input": {"type": "ORACLE_CARD_TAG", "page": page}},
                          op_name="SearchTags")
        block = data["tags"]
        out.extend(block["results"])
        total = block["total"]
        if not block["results"] or len(out) >= total:
            print(f"  enumerated {len(out)} oracle tags (total reported {total})",
                  file=sys.stderr)
            return out
        if page % 10 == 0:
            print(f"  ...tag list page {page} ({len(out)}/{total})",
                  file=sys.stderr)
        page += 1
        time.sleep(REQUEST_DELAY_S)


def fetch_tag_oracle_ids(client: TaggerClient, slug: str,
                         expected_total: int) -> list[str]:
    """Return every oracle_id tagged with this slug (one tag's direct cards).

    Walks all pages of taggings(descendants:false).
    """
    if expected_total <= 0:
        return []
    pages = (expected_total + TAGGINGS_PER_PAGE - 1) // TAGGINGS_PER_PAGE
    ids: list[str] = []
    for page in range(1, pages + 1):
        try:
            data = client.gql(FETCH_TAG_QUERY, {"slug": slug, "page": page},
                              op_name="FetchTag")
        except RuntimeError as e:
            # Tag may have been renamed/deleted between SearchTags and FetchTag.
            if "record not found" in str(e):
                print(f"  '{slug}': record not found, skipping", file=sys.stderr)
                return ids
            raise
        tg = (data or {}).get("tag") or {}
        results = ((tg.get("taggings") or {}).get("results")) or []
        for r in results:
            oid = ((r.get("card") or {}).get("oracleId"))
            if oid:
                ids.append(oid)
        time.sleep(REQUEST_DELAY_S)
    return ids


def load_progress() -> dict | None:
    if PROGRESS_PATH.exists():
        try:
            return json.loads(PROGRESS_PATH.read_text())
        except Exception:
            return None
    return None


def save_progress(progress: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress))
    tmp.replace(PROGRESS_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", action="store_true",
                    help="Pick up from the last saved progress file instead of "
                         "starting over. Useful after a crash or partial CI run.")
    ap.add_argument("--tag-limit", type=int, default=None,
                    help="Debug: only fetch the top N tags by taggingCount.")
    ap.add_argument("--out-dir", default=str(DATA_DIR))
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "oracle-tags.json.gz"
    meta_path = out_dir / "oracle-tags-meta.json"

    client = TaggerClient()

    progress = load_progress() if args.resume else None
    if progress and progress.get("tags"):
        tags_meta = progress["tags"]
        done = {t["slug"] for t in progress.get("done", [])}
        oracle_to_tags: dict[str, list[str]] = progress.get("oracle_to_tags", {})
        print(f"  resuming: {len(done)}/{len(tags_meta)} tags already done, "
              f"{len(oracle_to_tags)} cards collected", file=sys.stderr)
    else:
        print("Step 1/2: enumerating oracle tags...", file=sys.stderr)
        tags_meta = list_oracle_tags(client)
        tags_meta.sort(key=lambda t: t["slug"])
        done = set()
        oracle_to_tags = {}

    if args.tag_limit:
        # Take the top-N by taggingCount, but still do them in slug order.
        ranked = sorted(tags_meta, key=lambda t: -(t.get("taggingCount") or 0))[:args.tag_limit]
        keep = {t["slug"] for t in ranked}
        tags_meta = [t for t in tags_meta if t["slug"] in keep]
        print(f"  --tag-limit: keeping top {len(tags_meta)} tags",
              file=sys.stderr)

    print(f"Step 2/2: fetching taggings for {len(tags_meta)} tags "
          f"({sum(t['taggingCount'] for t in tags_meta):,} edges)...",
          file=sys.stderr)
    t0 = time.time()
    for i, tag in enumerate(tags_meta, 1):
        slug = tag["slug"]
        if slug in done:
            continue
        count = tag.get("taggingCount") or 0
        try:
            ids = fetch_tag_oracle_ids(client, slug, count)
        except Exception as e:
            print(f"  '{slug}' failed: {e}", file=sys.stderr)
            # Save progress and bail — caller can --resume.
            save_progress({"tags": tags_meta, "done": [{"slug": s} for s in done],
                           "oracle_to_tags": oracle_to_tags})
            raise
        for oid in ids:
            oracle_to_tags.setdefault(oid, []).append(slug)
        done.add(slug)
        # Periodic progress checkpoint + stderr line.
        if i % 25 == 0 or i == len(tags_meta):
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(tags_meta) - i) / rate if rate > 0 else 0
            print(f"  [{i:>4}/{len(tags_meta)}] {slug:<40s} "
                  f"({count:>5} cards) | {len(oracle_to_tags):,} unique cards | "
                  f"{rate:.1f} tags/s, ETA {eta/60:.1f}m",
                  file=sys.stderr)
            save_progress({"tags": tags_meta,
                           "done": [{"slug": s} for s in done],
                           "oracle_to_tags": oracle_to_tags})

    # Build slim artifact: tags=[slug...]; cards={oracle_id: [tag_idx,...]}
    print("Building slim artifact...", file=sys.stderr)
    tag_list = sorted({t for tags in oracle_to_tags.values() for t in tags})
    tag_to_idx = {s: i for i, s in enumerate(tag_list)}
    cards: dict[str, list[int]] = {}
    for oid, tags in oracle_to_tags.items():
        # Dedupe and sort indices for determinism.
        cards[oid] = sorted({tag_to_idx[t] for t in tags})

    artifact = {"tags": tag_list, "cards": cards}
    raw = json.dumps(artifact, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    with gzip.open(out_path, "wb", compresslevel=9) as f:
        f.write(raw)
    gz_size = out_path.stat().st_size

    sha = hashlib.sha256(out_path.read_bytes()).hexdigest()
    meta = {
        "source": "tagger.scryfall.com/graphql (ORACLE_CARD_TAG)",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tag_count": len(tag_list),
        "card_count": len(cards),
        "edge_count": sum(len(v) for v in cards.values()),
        "elapsed_minutes": round((time.time() - t0) / 60, 1),
        "raw_bytes": len(raw),
        "gz_bytes": gz_size,
        "sha256": sha,
        "note": ("Fetched via the undocumented tagger.scryfall.com GraphQL "
                 "endpoint (ORACLE_CARD_TAG, descendants=false). Refreshes "
                 "should run monthly."),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"  wrote {out_path}  ({gz_size/1e6:.1f} MB gz, {len(raw)/1e6:.1f} MB raw)",
          file=sys.stderr)
    print(f"  wrote {meta_path}", file=sys.stderr)
    print(f"  {len(tag_list)} tags, {len(cards):,} cards, "
          f"{sum(len(v) for v in cards.values()):,} edges",
          file=sys.stderr)

    # Clean up progress file on success.
    if PROGRESS_PATH.exists():
        PROGRESS_PATH.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
