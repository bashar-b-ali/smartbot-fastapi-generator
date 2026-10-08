from __future__ import annotations

from pathlib import Path

from app.services.runtime_validation import validate_runtime_project


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_runtime_validation_accepts_minimal_fastapi_app(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/ping")\ndef ping():\n    return {"status": "ok"}\n',
    )

    result = validate_runtime_project(tmp_path)

    assert result["passed"] is True
    assert result["isolated"] is True
    assert "/ping" in result["smoked_paths"]


def test_runtime_validation_rejects_import_failure(tmp_path: Path) -> None:
    write(tmp_path / "main.py", "from missing_module import nope\n")

    result = validate_runtime_project(tmp_path)

    assert result["passed"] is False
    assert result["errors"]


def test_runtime_validation_rejects_unavailable_declared_dependency(tmp_path: Path) -> None:
    write(tmp_path / "requirements.txt", "definitely-missing-runtime-package\n")
    write(
        tmp_path / "main.py",
        "from definitely_missing_runtime_package import nope\n",
    )

    result = validate_runtime_project(tmp_path)

    assert result["passed"] is False
    assert result["validation_environment_ready"] is False
    assert result["failure_category"] == "runtime_dependency_unavailable"
    assert result["checks"][0]["skipped"] is True


def test_runtime_validation_rejects_route_500(tmp_path: Path) -> None:
    write(
        tmp_path / "main.py",
        'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/boom")\ndef boom():\n    raise RuntimeError("boom")\n',
    )

    result = validate_runtime_project(tmp_path)

    assert result["passed"] is False
    assert any("/boom" in error for error in result["errors"])
