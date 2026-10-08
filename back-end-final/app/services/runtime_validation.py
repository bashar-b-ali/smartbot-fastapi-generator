from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

SAFE_METHODS = {"GET"}
SKIP_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def _normalized_requirement_names(root: Path) -> set[str]:
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        return set()
    names: set[str] = set()
    for raw_line in requirements.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line
        for marker in ("[", "<", ">", "=", "!", "~", ";"):
            name = name.split(marker, 1)[0]
        normalized = name.strip().lower().replace("-", "_")
        if normalized:
            names.add(normalized)
    return names


def _declared_missing_dependency(root: Path, exc: ModuleNotFoundError) -> str | None:
    missing = str(getattr(exc, "name", "") or "").lower().replace("-", "_")
    if missing and missing in _normalized_requirement_names(root):
        return missing
    return None


def _public_get_paths(app: FastAPI) -> list[str]:
    paths: list[str] = []
    schema = app.openapi()
    for path, operations in sorted((schema.get("paths") or {}).items()):
        if "{" in path or any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        if any(str(method).upper() in SAFE_METHODS for method in operations):
            paths.append(path)
    return paths[:12]


def _validate_runtime_project_in_process(root: str | Path) -> dict[str, Any]:
    started = perf_counter()
    project_root = Path(root)
    main_path = project_root / "main.py"
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    if not main_path.is_file():
        return {
            "passed": False,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "checks": [],
            "errors": ["main.py is missing"],
            "smoked_paths": [],
            "skipped_paths": [],
        }

    module_name = f"_generated_runtime_main_{uuid4().hex}"
    old_path = list(sys.path)
    old_modules = dict(sys.modules)
    old_cwd = Path.cwd()
    try:
        os.chdir(project_root)
        sys.path.insert(0, str(project_root))
        spec = importlib.util.spec_from_file_location(module_name, main_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load main.py import spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        app = getattr(module, "app", None)
        if not isinstance(app, FastAPI):
            raise RuntimeError("main.py must expose a FastAPI instance named app")
        smoked_paths: list[str] = []
        skipped_paths: list[str] = []
        with TestClient(app) as client:
            openapi = client.get("/openapi.json")
            checks.append({"name": "openapi", "passed": openapi.status_code < 500, "status_code": openapi.status_code})
            if openapi.status_code >= 500:
                errors.append(f"/openapi.json returned {openapi.status_code}")
            for path in _public_get_paths(app):
                try:
                    response = client.get(path)
                except Exception as exc:  # pragma: no cover - defensive reporting
                    checks.append({"name": "route", "path": path, "passed": False, "error": str(exc)})
                    errors.append(f"GET {path} raised {type(exc).__name__}: {exc}")
                    continue
                passed = response.status_code < 500
                checks.append({"name": "route", "path": path, "passed": passed, "status_code": response.status_code})
                if passed:
                    smoked_paths.append(path)
                else:
                    errors.append(f"GET {path} returned {response.status_code}")
        return {
            "passed": not errors and all(check.get("passed") for check in checks),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "checks": checks,
            "errors": errors,
            "smoked_paths": smoked_paths,
            "skipped_paths": skipped_paths,
        }
    except ModuleNotFoundError as exc:
        dependency = _declared_missing_dependency(project_root, exc)
        if dependency:
            return {
                "passed": False,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "checks": [
                    {
                        "name": "declared_dependency",
                        "passed": False,
                        "skipped": True,
                        "dependency": dependency,
                        "reason": "dependency_declared_but_not_installed_in_validator",
                    }
                ],
                "errors": [
                    f"Declared dependency '{dependency}' is unavailable in the runtime validator. "
                    "The generated application was not executed."
                ],
                "smoked_paths": [],
                "skipped_paths": [f"dependency:{dependency}"],
                "validation_environment_ready": False,
                "failure_category": "runtime_dependency_unavailable",
            }
        return {
            "passed": False,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "checks": checks,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "smoked_paths": [],
            "skipped_paths": [],
        }
    except Exception as exc:
        return {
            "passed": False,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "checks": checks,
            "errors": [f"{type(exc).__name__}: {exc}"],
            "smoked_paths": [],
            "skipped_paths": [],
        }
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
        for name in list(sys.modules):
            module = sys.modules.get(name)
            file_name = str(getattr(module, "__file__", "") or "")
            if name not in old_modules and file_name and Path(file_name).resolve().is_relative_to(project_root.resolve()):
                sys.modules.pop(name, None)
        sys.modules.pop(module_name, None)


def validate_runtime_project(root: str | Path) -> dict[str, Any]:
    """Validate generated code outside the API/worker process.

    The timeout protects only this disposable validation process. It is not a
    generation or edit deadline, and no already-applied project changes are
    reverted when validation fails.
    """
    project_root = Path(root).resolve()
    started = perf_counter()
    with tempfile.TemporaryDirectory(prefix="runtime_validation_result_") as temp_dir:
        result_path = Path(temp_dir) / "result.json"
        command = [
            sys.executable,
            "-m",
            "app.services.runtime_validation",
            "--worker",
            str(project_root),
            str(result_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "checks": [],
                "errors": ["Runtime validation exceeded the 60 second safety limit."],
                "smoked_paths": [],
                "skipped_paths": [],
                "isolated": True,
                "failure_category": "runtime_validation_timeout",
            }
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result["isolated"] = True
                result.setdefault("validation_environment_ready", not result.get("skipped_paths"))
                return result
            except (OSError, json.JSONDecodeError):
                pass
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        return {
            "passed": False,
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "checks": [],
            "errors": [
                f"Isolated runtime validator exited with code {completed.returncode}"
                + (f": {detail}" if detail else "")
            ],
            "smoked_paths": [],
            "skipped_paths": [],
            "isolated": True,
            "failure_category": "runtime_validator_crash",
        }


def _worker_main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        return 2
    result = _validate_runtime_project_in_process(Path(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())

