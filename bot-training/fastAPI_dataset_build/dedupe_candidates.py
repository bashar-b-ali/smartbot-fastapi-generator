import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

OUT_DIR = Path("./fastapi_dataset")
REPOS_DIR = OUT_DIR / "repos"
INPUT_PATH = OUT_DIR / "candidates.jsonl"
OUTPUT_PATH = OUT_DIR / "candidates_unique.jsonl"
SUMMARY_PATH = OUT_DIR / "candidates_unique_summary.json"

VARIANT_SUFFIX_RE = re.compile(r"(_an)?(_py\d+)?$")
PY_VERSION_RE = re.compile(r"_py(\d+)")


def normalize_source(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def content_hash(repo_slot: str, rel_path: str) -> str | None:
    p = REPOS_DIR / repo_slot / rel_path
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return hashlib.sha256(normalize_source(text).encode("utf-8")).hexdigest()


def variant_key(rel_path: str) -> tuple[str, int]:
    p = Path(rel_path)
    stem = p.stem
    m = PY_VERSION_RE.search(stem)
    rank = int(m.group(1)) if m else 0
    canonical = VARIANT_SUFFIX_RE.sub("", stem)
    parent = p.parent.as_posix()
    return f"{parent}/{canonical}", rank


def main() -> None:
    if not INPUT_PATH.exists():
        raise SystemExit(f"No {INPUT_PATH} - run extract_candidates.py first.")

    records = [json.loads(line) for line in INPUT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Loaded {len(records)} candidates")

    # --- Pass 1: exact content dedup ---
    by_hash: dict[str, list[dict]] = defaultdict(list)
    missing = 0
    for r in records:
        h = content_hash(r["repo_slot"], r["rel_path"])
        if h is None:
            missing += 1
            continue
        r["_hash"] = h
        by_hash[h].append(r)

    after_exact: list[dict] = []
    exact_collisions = 0
    for h, group in by_hash.items():
        if len(group) > 1:
            exact_collisions += len(group) - 1
            # Prefer the candidate from the highest-star repo.
            group.sort(key=lambda r: -(r.get("repo_stars") or 0))
        after_exact.append(group[0])

    print(f"  exact-content duplicates removed: {exact_collisions}")
    print(f"  unreadable files dropped:         {missing}")
    print(f"  after exact dedup:                {len(after_exact)}")

    # --- Pass 2: Python-version variant collapse ---
    by_variant: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in after_exact:
        canonical, rank = variant_key(r["rel_path"])
        r["_variant_rank"] = rank
        by_variant[(r["repo_slot"], canonical)].append(r)

    after_variant: list[dict] = []
    variant_collisions = 0
    for group in by_variant.values():
        if len(group) > 1:
            variant_collisions += len(group) - 1
            # Highest Python version, tiebreak by line count (more code = more learnable).
            group.sort(key=lambda r: (-r["_variant_rank"], -r["line_count"]))
        after_variant.append(group[0])

    print(f"  py-version variant duplicates removed: {variant_collisions}")
    print(f"  after variant collapse:                {len(after_variant)}")

    # Strip internal fields before writing
    for r in after_variant:
        r.pop("_hash", None)
        r.pop("_variant_rank", None)

    after_variant.sort(key=lambda r: (-(r.get("repo_stars") or 0), r["repo_slot"], r["rel_path"]))
    with OUTPUT_PATH.open("w", encoding="utf-8") as out:
        for r in after_variant:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    by_repo = Counter(r["repo_full_name"] for r in after_variant)
    by_lines = Counter()
    for r in after_variant:
        L = r["line_count"]
        bucket = "20-50" if L <= 50 else "51-100" if L <= 100 else "101-200" if L <= 200 else "201-400" if L <= 400 else "401-600"
        by_lines[bucket] += 1
    by_routes = Counter()
    for r in after_variant:
        n = r["route_count"]
        bucket = str(n) if n <= 5 else "6-10" if n <= 10 else "11+"
        by_routes[bucket] += 1

    summary = {
        "input_count": len(records),
        "exact_duplicates_removed": exact_collisions,
        "variant_duplicates_removed": variant_collisions,
        "unreadable_dropped": missing,
        "output_count": len(after_variant),
        "repos_contributing": len(by_repo),
        "top_repos": by_repo.most_common(20),
        "by_line_count": dict(by_lines.most_common()),
        "by_route_count": dict(by_routes.most_common()),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print(f"Output:  {OUTPUT_PATH}  ({len(after_variant)} unique candidates)")
    print(f"Summary: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
