import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATASET_DIR = ROOT / "fastapi_dataset"
REPOS_DIR = DATASET_DIR / "repos"
CANDIDATES_PATH = DATASET_DIR / "candidates_unique.jsonl"
CHECKPOINT_PATH = DATASET_DIR / "_prompts_done.json"

OUTPUT_DIR = ROOT / "training_data"
OUTPUT_PATH = OUTPUT_DIR / "fastapi_dataset.jsonl"

PROVIDERS_FILE = ROOT / "app" / "llm" / "providers.py"

SYSTEM_PROMPT = (
    "You are FastAPIBot, an expert FastAPI developer. Generate clean, secure, "
    "production-ready code. Follow FastAPI best practices, type hints, "
    "Pydantic v2, async-first. Use FastAPI 0.100+, Python 3.10+. Complete "
    "runnable code, no TODOs. Wrap files: ### FILE: name.py ###\n"
    "```python\n...\n```\n### END FILE ###"
)

BACKGEN_SYSTEM = (
    "You reverse-engineer training data for a code generation model. "
    "Given a FastAPI Python file, write the user request a developer would "
    "have written to produce this exact code. Write naturally, like a feature "
    "request a developer would type into a chat. Do not reveal that you are "
    "looking at code; phrase it as if requesting the code be built. "
    "Output ONLY the user request - no preamble, no quotes, no markdown."
)

BACKGEN_USER_TEMPLATE = (
    "Here is a single-file FastAPI application:\n\n"
    "```python\n{code}\n```\n\n"
    "Write ONE concise user request (2-6 sentences) that asks for exactly this "
    "app. Mention: the data models / Pydantic schemas, the endpoints and what "
    "they do, any auth or middleware, and any noteworthy behavior. Be specific "
    "but write in natural prose, not bullet points. Do NOT include code in your "
    "response. Output only the request text."
)

# Skip candidates whose source exceeds this many chars (rough proxy for tokens).
# 600-line cap from stage 1 means most are well under this; this is a safety net.
MAX_CODE_CHARS = 60_000


def load_providers_module():
    """Load app/llm/providers.py without triggering the package __init__."""
    spec = importlib.util.spec_from_file_location("_dataset_providers", PROVIDERS_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load providers from {PROVIDERS_FILE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"done_ids": [], "stats": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}


def save_checkpoint(state: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def candidate_id(rec: dict) -> str:
    return f"{rec['repo_slot']}/{rec['rel_path']}"


def load_code(rec: dict) -> str | None:
    p = REPOS_DIR / rec["repo_slot"] / rec["rel_path"]
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def build_assistant_content(code: str) -> str:
    return f"### FILE: main.py ###\n```python\n{code.rstrip()}\n```\n### END FILE ###"


def call_llm_with_retry(provider, Message, code: str, retries: int = 3):
    msgs = [
        Message(role="system", content=BACKGEN_SYSTEM),
        Message(role="user", content=BACKGEN_USER_TEMPLATE.format(code=code)),
    ]
    last_err = None
    for attempt in range(retries):
        try:
            return provider.complete(msgs, temperature=0.4, max_tokens=600)
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"    LLM error (attempt {attempt+1}/{retries}): {e}  retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} retries: {last_err}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--provider", default=None, help="anthropic|openai|google|ollama (default: auto)")
    ap.add_argument("--model", default=None, help="model id, e.g. claude-haiku-4-5-20251001")
    ap.add_argument("--limit", type=int, default=None, help="process only first N candidates")
    ap.add_argument("--dry-run", action="store_true", help="show what would be processed; no LLM calls")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    if not CANDIDATES_PATH.exists():
        sys.exit(f"No {CANDIDATES_PATH} - run extract_candidates.py and dedupe_candidates.py first.")
    if not PROVIDERS_FILE.exists():
        sys.exit(f"No {PROVIDERS_FILE} - cannot load LLM providers.")

    candidates = [
        json.loads(line) for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    print(f"Loaded {len(candidates)} candidates from {CANDIDATES_PATH.name}")

    state = load_checkpoint()
    done = set(state["done_ids"])
    pending = [c for c in candidates if candidate_id(c) not in done]
    if args.limit is not None:
        pending = pending[: args.limit]
    print(f"Already done:   {len(done)}")
    print(f"Pending now:    {len(pending)}")

    if args.dry_run:
        for c in pending[:5]:
            print(f"  would process: {candidate_id(c)}  L={c['line_count']} R={c['route_count']}")
        if len(pending) > 5:
            print(f"  ... +{len(pending) - 5} more")
        return

    if not pending:
        print("Nothing to do.")
        return

    providers = load_providers_module()
    provider = providers.get_provider(provider_type=args.provider, model=args.model)
    Message = providers.Message
    print(f"Provider: {type(provider).__name__}  model={provider.model}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    skipped_too_big = 0
    skipped_unreadable = 0
    written = 0

    with OUTPUT_PATH.open("a", encoding="utf-8") as out:
        for i, rec in enumerate(pending, 1):
            cid = candidate_id(rec)
            code = load_code(rec)
            if code is None:
                skipped_unreadable += 1
                done.add(cid)
                continue
            if len(code) > MAX_CODE_CHARS:
                skipped_too_big += 1
                done.add(cid)
                continue

            t0 = time.time()
            try:
                resp = call_llm_with_retry(provider, Message, code)
            except Exception as e:
                print(f"  [{i}/{len(pending)}] FAIL  {cid}  -- {e}", flush=True)
                continue

            user_request = resp.content.strip().strip('"').strip("'")
            if not user_request or len(user_request) < 30:
                print(f"  [{i}/{len(pending)}] EMPTY {cid}  -- skipping", flush=True)
                done.add(cid)
                continue

            pair = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_request},
                    {"role": "assistant", "content": build_assistant_content(code)},
                ],
                "_meta": {
                    "candidate_id": cid,
                    "repo": rec["repo_full_name"],
                    "stars": rec.get("repo_stars"),
                    "license": rec["license"],
                    "line_count": rec["line_count"],
                    "route_count": rec["route_count"],
                },
            }
            out.write(json.dumps(pair, ensure_ascii=False) + "\n")
            out.flush()
            written += 1

            state["stats"]["calls"] += 1
            state["stats"]["input_tokens"] += resp.input_tokens
            state["stats"]["output_tokens"] += resp.output_tokens
            state["stats"]["cost_usd"] += resp.cost_estimate
            done.add(cid)

            elapsed = time.time() - t0
            print(
                f"  [{i:>3}/{len(pending)}] OK   {cid:60.60}  "
                f"in={resp.input_tokens:>5} out={resp.output_tokens:>3}  "
                f"${state['stats']['cost_usd']:.3f}  {elapsed:.1f}s",
                flush=True,
            )

            # Persist checkpoint every 5 calls.
            if written % 5 == 0:
                state["done_ids"] = sorted(done)
                save_checkpoint(state)

    state["done_ids"] = sorted(done)
    save_checkpoint(state)

    total_min = (time.time() - started) / 60
    print()
    print(f"Done in {total_min:.1f}m")
    print(f"  written:           {written}")
    print(f"  skipped (too big): {skipped_too_big}")
    print(f"  skipped (read err):{skipped_unreadable}")
    print(f"  total LLM calls:   {state['stats']['calls']}")
    print(f"  input tokens:      {state['stats']['input_tokens']:,}")
    print(f"  output tokens:     {state['stats']['output_tokens']:,}")
    print(f"  est. cost:         ${state['stats']['cost_usd']:.3f}")
    print(f"  output:            {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
