import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from github import Github, RateLimitExceededException

GITHUB_TOKEN = os.environ.get(
    "GITHUB_TOKEN",
    "01A4RSREQ05uLK2DGanbSW_Q1oj97oP3vX2Hpn7OwhkAJSsqWcbiaANjQQXTkXBm7q5PZP2DV3jPtr7mjC",
)

OUT_DIR = Path("./fastapi_dataset")
INDEX_PATH = OUT_DIR / "repos_index.json"

# Each entry becomes one search query: "fastapi language:python stars:LO..HI".
# Each query returns up to 1000 results. If you see "HIT 1000 CAP" in the
# output for any bucket, split that range into smaller pieces and rerun.
STAR_BUCKETS = [
    (2,    3),
    (4,    5),
    (6,    8),
    (9,   12),
    (13,  18),
    (19,  28),
    (29,  45),
    (46,  75),
    (76, 130),
    (131, 250),
    (251, 600),
    (601, 2000),
    (2001, 100000),
]


def wait_for_reset(g: Github) -> None:
    try:
        reset = g.get_rate_limit().core.reset.replace(tzinfo=timezone.utc)
        wait = max(5, int((reset - datetime.now(timezone.utc)).total_seconds()) + 5)
    except Exception:
        wait = 60
    print(f"  rate limit hit, sleeping {wait}s", flush=True)
    time.sleep(wait)


def save(seen: dict[str, dict]) -> None:
    rows = sorted(seen.values(), key=lambda r: -r["stars"])
    for n, r in enumerate(rows, 1):
        r["n"] = n
    INDEX_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def discover() -> list[dict]:
    if not GITHUB_TOKEN:
        raise SystemExit("Set GITHUB_TOKEN env var.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seen: dict[str, dict] = {}
    if INDEX_PATH.exists():
        for entry in json.loads(INDEX_PATH.read_text(encoding="utf-8")):
            seen[entry["full_name"]] = entry
        print(f"Resuming - {len(seen)} repos already in index")

    g = Github(GITHUB_TOKEN, per_page=100, retry=3)

    for lo, hi in STAR_BUCKETS:
        query = f"fastapi language:python stars:{lo}..{hi}"
        print(f"\n[bucket {lo}..{hi}]  {query}")
        try:
            results = g.search_repositories(query, sort="stars", order="desc")
            count = results.totalCount
        except RateLimitExceededException:
            wait_for_reset(g)
            results = g.search_repositories(query, sort="stars", order="desc")
            count = results.totalCount
        cap_warning = "  (HIT 1000 CAP - split this bucket)" if count >= 1000 else ""
        print(f"  total available: {count}{cap_warning}")

        added = 0
        while True:
            try:
                for repo in results:
                    if repo.full_name in seen:
                        continue
                    seen[repo.full_name] = {
                        "full_name": repo.full_name,
                        "clone_url": repo.clone_url,
                        "stars": repo.stargazers_count,
                        "default_branch": repo.default_branch,
                    }
                    added += 1
                    if added % 100 == 0:
                        print(f"  ... {added} new in this bucket ({len(seen)} total)", flush=True)
                        save(seen)
                break
            except RateLimitExceededException:
                wait_for_reset(g)
                continue

        print(f"  +{added} new  ({len(seen)} total)")
        save(seen)

    print(f"\nDone. {len(seen)} unique repos -> {INDEX_PATH}")
    return list(seen.values())


if __name__ == "__main__":
    discover()
