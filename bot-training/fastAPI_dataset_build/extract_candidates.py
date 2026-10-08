import ast
import json
import re
from collections import Counter
from pathlib import Path

OUT_DIR = Path("./fastapi_dataset")
REPOS_DIR = OUT_DIR / "repos"
INDEX_PATH = OUT_DIR / "repos_index.json"
CANDIDATES_PATH = OUT_DIR / "candidates.jsonl"
SUMMARY_PATH = OUT_DIR / "candidates_summary.json"

MIN_LINES = 20
MAX_LINES = 600
MIN_ROUTES = 2
MAX_FILE_BYTES = 200_000  # skip absurdly large files fast

PERMISSIVE = {"MIT", "Apache-2.0", "BSD", "ISC", "Unlicense", "MPL-2.0"}

# Paths inside a repo we never want to harvest from.
SKIP_DIR_PARTS = {
    "test", "tests", "testing",
    ".git", "node_modules", "venv", ".venv", "env",
    "build", "dist", "site-packages",
    "migrations", "alembic",
    "__pycache__",
}
SKIP_FILENAMES = {
    "__init__.py", "setup.py", "conftest.py", "manage.py",
}

ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
APP_CTORS = {"FastAPI", "APIRouter"}
PYDANTIC_RE = re.compile(r"\bBaseModel\b")
ASYNC_RE = re.compile(r"^\s*async\s+def\s", re.MULTILINE)

# Cheap secret heuristics. False positives are fine, false negatives are not.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),               # OpenAI
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),          # Anthropic
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),              # GitHub PAT classic
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),      # GitHub PAT fine-grained
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS access key
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),      # Slack
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),             # Google API
]

# Map first-line license header -> SPDX-ish tag.
LICENSE_HEADERS = [
    (re.compile(r"\bMIT License\b", re.I),                 "MIT"),
    (re.compile(r"\bApache License\b", re.I),              "Apache-2.0"),
    (re.compile(r"\bBSD\b.{0,40}License", re.I | re.S),    "BSD"),
    (re.compile(r"\bISC License\b", re.I),                 "ISC"),
    (re.compile(r"\bUnlicense\b", re.I),                   "Unlicense"),
    (re.compile(r"\bMozilla Public License\b", re.I),      "MPL-2.0"),
    (re.compile(r"\bGNU Affero General Public", re.I),     "AGPL"),
    (re.compile(r"\bGNU General Public License\b", re.I),  "GPL"),
    (re.compile(r"\bGNU Lesser General Public", re.I),     "LGPL"),
]


def detect_license(repo_dir: Path) -> str:
    """Read LICENSE/LICENCE/COPYING and return a coarse license tag."""
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md",
                 "LICENCE", "LICENCE.txt",
                 "COPYING", "COPYING.txt"):
        p = repo_dir / name
        if p.exists():
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:3000]
            except OSError:
                continue
            for rx, tag in LICENSE_HEADERS:
                if rx.search(head):
                    return tag
            return "Unknown"
    return "None"


def should_skip_path(rel: Path) -> bool:
    if rel.name in SKIP_FILENAMES:
        return True
    if rel.name.startswith("test_") or rel.name.endswith("_test.py"):
        return True
    parts_lower = {p.lower() for p in rel.parts}
    return bool(parts_lower & SKIP_DIR_PARTS)


def has_secret(text: str) -> bool:
    return any(rx.search(text) for rx in SECRET_PATTERNS)


def collect_imports(tree: ast.AST) -> tuple[list[str], bool]:
    """Top-level package names + whether the module imports from fastapi."""
    pkgs: set[str] = set()
    imports_fastapi = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                pkgs.add(root)
                if root == "fastapi":
                    imports_fastapi = True
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            pkgs.add(root)
            if root == "fastapi":
                imports_fastapi = True
    return sorted(pkgs), imports_fastapi


def find_app_bindings(tree: ast.Module) -> dict[str, str]:
    """Variable names bound to FastAPI()/APIRouter() at module level.

    Returns {name: ctor} e.g. {"app": "FastAPI", "router": "APIRouter"}.
    """
    bindings: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        ctor = None
        if isinstance(value.func, ast.Name) and value.func.id in APP_CTORS:
            ctor = value.func.id
        elif isinstance(value.func, ast.Attribute) and value.func.attr in APP_CTORS:
            ctor = value.func.attr
        if ctor is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                bindings[tgt.id] = ctor
    return bindings


def count_routes(tree: ast.Module, bindings: set[str]) -> tuple[int, list[str]]:
    """Count function decorators of the form @<binding>.<verb>(...)."""
    total = 0
    verbs: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            attr = call.func if call else dec
            if not isinstance(attr, ast.Attribute):
                continue
            if not isinstance(attr.value, ast.Name):
                continue
            if attr.value.id in bindings and attr.attr in ROUTE_VERBS:
                total += 1
                verbs.append(attr.attr)
    return total, verbs


def evaluate_file(path: Path, source: str) -> dict | None:
    """Return a candidate record or None if the file doesn't qualify."""
    if "fastapi" not in source:
        return None

    line_count = source.count("\n") + 1
    if not (MIN_LINES <= line_count <= MAX_LINES):
        return None

    if has_secret(source):
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    imports, imports_fastapi = collect_imports(tree)
    if not imports_fastapi:
        return None

    bindings = find_app_bindings(tree)
    if not bindings:
        return None
    has_fastapi_app = any(ctor == "FastAPI" for ctor in bindings.values())
    if not has_fastapi_app:
        return None

    route_count, verbs = count_routes(tree, set(bindings))
    if route_count < MIN_ROUTES:
        return None

    return {
        "line_count": line_count,
        "route_count": route_count,
        "route_verbs": sorted(Counter(verbs).items()),
        "app_bindings": bindings,
        "has_pydantic": bool(PYDANTIC_RE.search(source)),
        "has_async": bool(ASYNC_RE.search(source)),
        "imports": imports,
        "byte_size": len(source.encode("utf-8", errors="replace")),
    }


def iter_python_files(repo_dir: Path):
    for p in repo_dir.rglob("*.py"):
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        rel = p.relative_to(repo_dir)
        if should_skip_path(rel):
            continue
        yield p, rel


def load_repo_metadata() -> dict[str, dict]:
    """slot-folder-name -> {full_name, stars, n} from repos_index.json."""
    if not INDEX_PATH.exists():
        return {}
    rows = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    by_slot: dict[str, dict] = {}
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    for r in rows:
        slug = safe.sub("_", r["full_name"].replace("/", "_"))
        by_slot[f"{r['n']:04d}_{slug}"] = r
    return by_slot


def main() -> None:
    if not REPOS_DIR.exists():
        raise SystemExit(f"No {REPOS_DIR} - run dataset_making.py first.")

    repo_meta = load_repo_metadata()
    repo_dirs = sorted(d for d in REPOS_DIR.iterdir() if d.is_dir())
    print(f"Scanning {len(repo_dirs)} repos -> {CANDIDATES_PATH}")

    license_counts: Counter[str] = Counter()
    repo_status: Counter[str] = Counter()  # permissive / non-permissive / no-license
    candidates_per_repo: Counter[str] = Counter()
    files_scanned = 0
    files_qualified = 0

    with CANDIDATES_PATH.open("w", encoding="utf-8") as out:
        for i, repo_dir in enumerate(repo_dirs, 1):
            slot = repo_dir.name
            meta = repo_meta.get(slot, {})
            license_tag = detect_license(repo_dir)
            license_counts[license_tag] += 1

            if license_tag in PERMISSIVE:
                repo_status["permissive"] += 1
            elif license_tag in {"None", "Unknown"}:
                repo_status["no_or_unknown_license"] += 1
                # Skip - we won't redistribute code we can't verify.
                if i % 25 == 0 or i == len(repo_dirs):
                    print(f"  [{i:>3}/{len(repo_dirs)}] {slot}  license={license_tag}  (skipped)", flush=True)
                continue
            else:
                repo_status["non_permissive"] += 1
                if i % 25 == 0 or i == len(repo_dirs):
                    print(f"  [{i:>3}/{len(repo_dirs)}] {slot}  license={license_tag}  (skipped)", flush=True)
                continue

            repo_hits = 0
            for path, rel in iter_python_files(repo_dir):
                files_scanned += 1
                try:
                    source = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                info = evaluate_file(path, source)
                if info is None:
                    continue
                files_qualified += 1
                repo_hits += 1
                record = {
                    "repo_slot": slot,
                    "repo_full_name": meta.get("full_name", slot),
                    "repo_stars": meta.get("stars"),
                    "license": license_tag,
                    "rel_path": rel.as_posix(),
                    **info,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")

            candidates_per_repo[slot] = repo_hits
            if i % 25 == 0 or i == len(repo_dirs):
                print(f"  [{i:>3}/{len(repo_dirs)}] {slot}  license={license_tag}  hits={repo_hits}", flush=True)

    summary = {
        "repos_total": len(repo_dirs),
        "repos_by_status": dict(repo_status),
        "license_counts": dict(license_counts.most_common()),
        "files_scanned": files_scanned,
        "files_qualified": files_qualified,
        "repos_with_at_least_one_candidate": sum(1 for v in candidates_per_repo.values() if v),
        "top_repos_by_candidates": candidates_per_repo.most_common(30),
        "thresholds": {
            "min_lines": MIN_LINES,
            "max_lines": MAX_LINES,
            "min_routes": MIN_ROUTES,
            "permissive_licenses": sorted(PERMISSIVE),
        },
    }
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print(f"Files scanned:    {files_scanned}")
    print(f"Files qualified:  {files_qualified}")
    print(f"Repos contributing candidates: "
          f"{summary['repos_with_at_least_one_candidate']} / {len(repo_dirs)}")
    print(f"  manifest -> {CANDIDATES_PATH}")
    print(f"  summary  -> {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
