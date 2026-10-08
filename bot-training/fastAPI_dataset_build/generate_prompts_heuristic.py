import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DATASET_DIR = ROOT / "fastapi_dataset"
REPOS_DIR = DATASET_DIR / "repos"
CANDIDATES_PATH = DATASET_DIR / "candidates_unique.jsonl"

OUTPUT_DIR = ROOT / "training_data"
OUTPUT_PATH = OUTPUT_DIR / "fastapi_dataset_heuristic.jsonl"

SYSTEM_PROMPT = (
    "You are FastAPIBot, an expert FastAPI developer. Generate clean, secure, "
    "production-ready code. Follow FastAPI best practices, type hints, "
    "Pydantic v2, async-first. Use FastAPI 0.100+, Python 3.10+. Complete "
    "runnable code, no TODOs. Wrap files: ### FILE: name.py ###\n"
    "```python\n...\n```\n### END FILE ###"
)

ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket"}
APP_CTORS = {"FastAPI", "APIRouter"}

# Map common imports to short feature phrases.
LIBRARY_HINTS = {
    "bcrypt":          "password hashing with bcrypt",
    "passlib":         "password hashing with passlib",
    "argon2":          "password hashing with argon2",
    "jwt":             "JWT authentication",
    "jose":            "JWT authentication via python-jose",
    "authlib":         "OAuth via Authlib",
    "sqlalchemy":      "SQLAlchemy ORM",
    "sqlmodel":        "SQLModel ORM",
    "tortoise":        "Tortoise ORM",
    "ormar":           "Ormar ORM",
    "asyncpg":         "async PostgreSQL via asyncpg",
    "aiomysql":        "async MySQL",
    "motor":           "async MongoDB via Motor",
    "pymongo":         "MongoDB",
    "redis":           "Redis caching",
    "aioredis":        "async Redis",
    "celery":          "Celery background tasks",
    "boto3":           "AWS S3 integration",
    "stripe":          "Stripe payments",
    "openai":          "OpenAI API integration",
    "anthropic":       "Anthropic API integration",
    "langchain":       "LangChain orchestration",
    "websockets":      "WebSocket connections",
    "starlette":       "Starlette middleware",
}


def get_str_kwarg(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def extract_app_meta(tree: ast.Module) -> tuple[str | None, str | None, set[str]]:
    """Return (title, description, app_var_names)."""
    title = description = None
    app_vars: set[str] = set()
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
        if not ctor:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                app_vars.add(tgt.id)
        if ctor == "FastAPI":
            title = title or get_str_kwarg(value, "title")
            description = description or get_str_kwarg(value, "description")
    return title, description, app_vars


SCHEMA_BASES = {"BaseModel", "BaseSettings", "SQLModel", "Document"}


def is_basemodel_class(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id in SCHEMA_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in SCHEMA_BASES:
            return True
        # SQLModel(table=True), DeclarativeBase, Base via subscript - keep heuristic loose
        if isinstance(base, ast.Call):
            f = base.func
            if isinstance(f, ast.Name) and f.id in SCHEMA_BASES:
                return True
            if isinstance(f, ast.Attribute) and f.attr in SCHEMA_BASES:
                return True
    return False


def is_enum_class(cls: ast.ClassDef) -> bool:
    for base in cls.bases:
        if isinstance(base, ast.Name) and "Enum" in base.id:
            return True
        if isinstance(base, ast.Attribute) and "Enum" in base.attr:
            return True
    return False


def class_field_names(cls: ast.ClassDef) -> list[str]:
    """For Pydantic models: typed assignments. For enums: simple assignments."""
    names: list[str] = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    names.append(tgt.id)
    # Filter out dunders and common nested config classes
    return [n for n in names if not n.startswith("_") and n not in {"Config", "Meta"}]


def extract_models_and_enums(tree: ast.Module) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    models: list[tuple[str, list[str]]] = []
    enums: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if is_basemodel_class(node):
            models.append((node.name, class_field_names(node)))
        elif is_enum_class(node):
            enums.append((node.name, class_field_names(node)))
    return models, enums


def extract_routes(tree: ast.Module, app_vars: set[str]) -> list[dict]:
    """One dict per route: verb, path, handler, doc, is_async."""
    routes: list[dict] = []
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
            if attr.value.id not in app_vars or attr.attr not in ROUTE_VERBS:
                continue
            path = "/"
            if call and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                path = call.args[0].value
            doc = ast.get_docstring(node) or ""
            doc_first = doc.strip().splitlines()[0].strip() if doc else ""
            routes.append({
                "verb": attr.attr,
                "path": path,
                "handler": node.name,
                "doc": doc_first[:120],
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
    return routes


def collect_top_imports(tree: ast.Module) -> set[str]:
    pkgs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                pkgs.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            pkgs.add(node.module.split(".")[0])
    return pkgs


def feature_hints(imports: set[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for pkg, phrase in LIBRARY_HINTS.items():
        if pkg in imports and phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out


def humanize_field_list(fields: list[str], max_show: int = 6) -> str:
    if not fields:
        return ""
    if len(fields) <= max_show:
        return ", ".join(fields)
    head = ", ".join(fields[:max_show])
    return f"{head}, and {len(fields) - max_show} more"


def render_prompt(tree: ast.Module, imports: set[str]) -> str:
    title, description, app_vars = extract_app_meta(tree)
    models, enums = extract_models_and_enums(tree)
    routes = extract_routes(tree, app_vars)
    hints = feature_hints(imports)

    sentences: list[str] = []

    intro = "Build a FastAPI application"
    if title:
        intro += f" titled \"{title}\""
    intro += " in a single Python file."
    sentences.append(intro)

    if description:
        clean = description.strip().rstrip(".")
        if clean:
            sentences.append(f"It is described as: {clean}.")

    if models:
        if len(models) == 1:
            name, fields = models[0]
            sentences.append(
                f"Define a Pydantic model {name} with fields {humanize_field_list(fields)}."
                if fields else f"Define a Pydantic model {name}."
            )
        else:
            parts = []
            for name, fields in models[:5]:
                if fields:
                    parts.append(f"{name} ({humanize_field_list(fields)})")
                else:
                    parts.append(name)
            extra = "" if len(models) <= 5 else f", plus {len(models) - 5} more model(s)"
            sentences.append(f"Define Pydantic models: {'; '.join(parts)}{extra}.")

    if enums:
        parts = []
        for name, values in enums[:3]:
            vlist = "/".join(values[:5]) if values else ""
            parts.append(f"{name}" + (f" with values {vlist}" if vlist else ""))
        sentences.append(f"Include enum(s): {', '.join(parts)}.")

    if routes:
        verb_counts: dict[str, int] = {}
        for r in routes:
            verb_counts[r["verb"]] = verb_counts.get(r["verb"], 0) + 1

        if len(routes) <= 4:
            line_parts = []
            for r in routes:
                piece = f"{r['verb'].upper()} {r['path']}"
                if r["doc"]:
                    piece += f" ({r['doc']})"
                line_parts.append(piece)
            sentences.append("Expose endpoints: " + "; ".join(line_parts) + ".")
        else:
            sample = routes[:6]
            shown = ", ".join(f"{r['verb'].upper()} {r['path']}" for r in sample)
            mix = ", ".join(f"{n} {v.upper()}" for v, n in verb_counts.items())
            sentences.append(
                f"Expose {len(routes)} endpoints ({mix}), including {shown}"
                f"{', and others' if len(routes) > 6 else ''}."
            )

    has_async_routes = any(r["is_async"] for r in routes)
    if has_async_routes:
        sentences.append("Use async route handlers where appropriate.")

    if hints:
        if len(hints) == 1:
            sentences.append(f"Use {hints[0]}.")
        else:
            sentences.append("Use " + ", ".join(hints[:-1]) + f", and {hints[-1]}.")

    sentences.append("Put everything in one file.")
    return " ".join(sentences)


def build_assistant_content(code: str) -> str:
    return f"### FILE: main.py ###\n```python\n{code.rstrip()}\n```\n### END FILE ###"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limit", type=int, default=None, help="process only first N candidates")
    ap.add_argument("--print-only", action="store_true", help="show prompts on stdout, don't write file")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    if not CANDIDATES_PATH.exists():
        sys.exit(f"No {CANDIDATES_PATH} - run extract_candidates.py and dedupe_candidates.py first.")

    candidates = [
        json.loads(l) for l in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    if args.limit is not None:
        candidates = candidates[: args.limit]
    print(f"Processing {len(candidates)} candidates")

    written = skipped = 0
    out_handle = None
    if not args.print_only:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_handle = OUTPUT_PATH.open("w", encoding="utf-8")

    for i, rec in enumerate(candidates, 1):
        path = REPOS_DIR / rec["repo_slot"] / rec["rel_path"]
        try:
            code = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  [{i}] SKIP unreadable: {path}  -- {e}")
            skipped += 1
            continue

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            print(f"  [{i}] SKIP unparsable: {path}  -- {e}")
            skipped += 1
            continue

        imports = collect_top_imports(tree)
        prompt = render_prompt(tree, imports)

        pair = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": build_assistant_content(code)},
            ],
            "_meta": {
                "candidate_id": f"{rec['repo_slot']}/{rec['rel_path']}",
                "repo": rec["repo_full_name"],
                "stars": rec.get("repo_stars"),
                "license": rec["license"],
                "line_count": rec["line_count"],
                "route_count": rec["route_count"],
                "source": "heuristic",
            },
        }

        if args.print_only:
            print(f"\n--- [{i}] {rec['repo_full_name']} :: {rec['rel_path']}  (L={rec['line_count']} R={rec['route_count']}) ---")
            print("USER PROMPT:")
            print(f"  {prompt}")
            print(f"ASSISTANT (first 200 chars of code): {code.strip()[:200]}...")
        else:
            out_handle.write(json.dumps(pair, ensure_ascii=False) + "\n")
        written += 1

    if out_handle:
        out_handle.close()

    print()
    print(f"Wrote {written} pairs, skipped {skipped}")
    if not args.print_only:
        print(f"  output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
