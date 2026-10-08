import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

GITHUB_TOKEN = os.environ.get(
    "GITHUB_TOKEN",
    "01A4RSREQ0KOpDdBtsj0pl_I1RU2q649Ak992dd30rHVxJUq4XOuW2KLk0Hcn96RLzR744PX22CEwfmOiq",
)

OUT_DIR = Path("./fastapi_dataset")
INDEX_PATH = OUT_DIR / "repos_index.json"
REPOS_DIR = OUT_DIR / "repos"
DOWNLOAD_LOG = OUT_DIR / "_download_log.json"

CLONE_DEPTH = 1        # shallow clone (latest commit only); set to 0 for full history
CLONE_TIMEOUT = 300    # seconds per repo
LOG_FLUSH_EVERY = 5    # save the log to disk every N completed clones

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def slot_dir(entry: dict) -> Path:
    safe = _SAFE.sub("_", entry["full_name"].replace("/", "_"))
    return REPOS_DIR / f"{entry['n']:04d}_{safe}"


def is_done(d: Path) -> bool:
    return d.exists() and any(d.iterdir())


def rmtree_safe(p: Path) -> None:
    """Windows-safe rmtree (handles read-only files inside .git)."""
    def _on_error(func, path, _info):
        try:
            os.chmod(path, 0o700)
            func(path)
        except OSError:
            pass
    if p.exists():
        shutil.rmtree(p, onerror=_on_error)


def load_log() -> dict:
    if DOWNLOAD_LOG.exists():
        try:
            return json.loads(DOWNLOAD_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_log(log: dict) -> None:
    DOWNLOAD_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")

def clone_one(entry: dict) -> tuple[str, str]:
    """Clone one repo. Returns (status, detail). status ∈ {ok, exists, error}."""
    target = slot_dir(entry)
    if is_done(target):
        return "exists", ""

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    rmtree_safe(tmp)

    # Fix: Use proper GitHub token authentication
    clone_url = entry["clone_url"]
    if GITHUB_TOKEN and clone_url.startswith("https://github.com/"):
        # Insert token into URL (most reliable method)
        clone_url = clone_url.replace("https://", f"https://{GITHUB_TOKEN}@")
    
    cmd = ["git", "clone"]
    if CLONE_DEPTH:
        cmd += ["--depth", str(CLONE_DEPTH)]
    cmd += ["--quiet", clone_url, str(tmp)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=CLONE_TIMEOUT)
        tmp.rename(target)
        return "ok", ""
    except subprocess.CalledProcessError as e:
        rmtree_safe(tmp)
        # Better error reporting
        stderr = (e.stderr or b"").decode(errors="replace").strip()
        stdout = (e.stdout or b"").decode(errors="replace").strip()
        error_msg = stderr or stdout or "git failed"
        print(f"DEBUG - Full error: {error_msg}")  # Debug print
        return "error", error_msg[:500]  # Longer error message
    except subprocess.TimeoutExpired:
        rmtree_safe(tmp)
        return "error", "timeout"

def download() -> None:
    if not INDEX_PATH.exists():
        raise SystemExit(f"No {INDEX_PATH} - run discover_repos.py first.")
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    log = load_log()
    todo = [e for e in index if not is_done(slot_dir(e))]
    print(f"Pending: {len(todo)} / {len(index)} total")
    if not todo:
        print("Nothing to do.")
        return

    ok = exists = fail = 0
    started = time.time()

    for i, entry in enumerate(todo, 1):
        ts = datetime.now().strftime("%H:%M:%S")
        status, detail = clone_one(entry)
        log[entry["full_name"]] = {
            "n": entry["n"],
            "status": status,
            "detail": detail,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

        if status == "ok":
            ok += 1
            mark = "OK  "
        elif status == "exists":
            exists += 1
            mark = "SKIP"
        else:
            fail += 1
            mark = "FAIL"

        line = f"[{ts}] {i:>5}/{len(todo)} {mark}  {entry['n']:04d} {entry['full_name']}"
        if detail:
            line += f"  -- {detail}"
        print(line, flush=True)

        if i % LOG_FLUSH_EVERY == 0:
            save_log(log)

    save_log(log)
    elapsed = time.time() - started
    print(f"\nDone in {elapsed/60:.1f}m  ok={ok}  already={exists}  failed={fail}")
    print(f"  repos -> {REPOS_DIR}")
    print(f"  log   -> {DOWNLOAD_LOG}")


if __name__ == "__main__":
    download()
