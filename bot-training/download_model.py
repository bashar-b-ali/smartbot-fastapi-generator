"""Download and verify the fine-tuned GGUF from its public Google Drive file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "models" / "fastAPI_Model" / "MANIFEST.json"
CHUNK_SIZE = 8 * 1024 * 1024


def load_manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify(path: Path, manifest: dict[str, Any], *, quiet: bool = False) -> bool:
    expected_size = int(manifest["artifact_size_bytes"])
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    with path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            return False
    expected_hash = manifest.get("sha256")
    actual_hash = sha256(path)
    if expected_hash and actual_hash != str(expected_hash).upper():
        return False
    if not quiet:
        print(f"Verified model: {path}")
        print(f"SHA-256: {actual_hash}")
    return True


def progress(downloaded: int, initial: int, total: int, started_at: float) -> None:
    elapsed = max(time.monotonic() - started_at, 0.001)
    speed = (downloaded - initial) / elapsed / (1024 * 1024)
    percent = min(downloaded / total * 100, 100) if total else 0
    print(
        f"\rDownloading: {downloaded / (1024**3):.2f} / "
        f"{total / (1024**3):.2f} GiB ({percent:5.1f}%) at {speed:.1f} MiB/s",
        end="",
        flush=True,
    )


def install_download(partial: Path, destination: Path) -> None:
    if destination.exists():
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup = destination.with_name(f"{destination.name}.backup-{timestamp}")
        suffix = 1
        while backup.exists():
            backup = destination.with_name(
                f"{destination.name}.backup-{timestamp}-{suffix}"
            )
            suffix += 1
        destination.replace(backup)
        print(f"Preserved the different local model as: {backup}")
    os.replace(partial, destination)


def download(file_id: str, destination: Path, expected_size: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0

    free_bytes = shutil.disk_usage(destination.parent).free
    remaining = max(expected_size - existing, 0)
    if free_bytes < remaining + 100 * 1024 * 1024:
        raise RuntimeError(
            f"Not enough free disk space. Need at least {remaining / (1024**3):.2f} GiB "
            "plus 100 MiB."
        )

    url = (
        "https://drive.usercontent.google.com/download"
        f"?id={file_id}&export=download&confirm=t"
    )
    headers = {"User-Agent": "SmartBot-Model-Installer/1.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"

    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and verify(partial, load_manifest(), quiet=True):
            install_download(partial, destination)
            return
        raise RuntimeError(f"Google Drive download failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to Google Drive: {exc.reason}") from exc

    with response:
        content_type = response.headers.get_content_type()
        final_url = response.geturl()
        if content_type in {"text/html", "application/xhtml+xml"} or "accounts.google.com" in final_url:
            raise RuntimeError(
                "Google Drive did not return the model. Set the file's General access to "
                "'Anyone with the link' (Viewer), then retry."
            )

        status = getattr(response, "status", 200)
        append = existing > 0 and status == 206
        if existing and not append:
            existing = 0
        mode = "ab" if append else "wb"
        downloaded = existing
        initial = existing
        started_at = time.monotonic()
        with partial.open(mode) as output:
            while chunk := response.read(CHUNK_SIZE):
                output.write(chunk)
                downloaded += len(chunk)
                progress(downloaded, initial, expected_size, started_at)
    print()

    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"Downloaded size mismatch: expected {expected_size}, got {partial.stat().st_size}. "
            "The partial download was kept so a retry can resume it."
        )
    manifest = load_manifest()
    with partial.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise RuntimeError("The downloaded file is not a GGUF model.")
    actual_hash = sha256(partial)
    expected_hash = manifest.get("sha256")
    if expected_hash and actual_hash != str(expected_hash).upper():
        raise RuntimeError(
            f"Downloaded SHA-256 mismatch: expected {expected_hash}, got {actual_hash}. "
            f"Delete {partial} before retrying if the Drive file was replaced."
        )
    print(f"Downloaded SHA-256: {actual_hash}")
    install_download(partial, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the local GGUF without downloading it",
    )
    args = parser.parse_args()

    manifest = load_manifest()
    destination = ROOT / str(manifest["artifact"])
    if verify(destination, manifest):
        return 0
    if args.verify_only:
        print(f"Model is missing or invalid: {destination}", file=sys.stderr)
        return 1

    file_id = str(manifest.get("google_drive_file_id", "")).strip()
    if not file_id:
        print("The manifest does not define google_drive_file_id.", file=sys.stderr)
        return 1

    print(f"Downloading fine-tuned model to: {destination}")
    try:
        download(file_id, destination, int(manifest["artifact_size_bytes"]))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not verify(destination, manifest):
        print("Downloaded model failed final verification.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
