from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_run():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ablation_study.py"
    spec = importlib.util.spec_from_file_location("ablation_study", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


def test_ablation_study_runs_deterministically() -> None:
    rows = _load_run()()

    assert {row["name"] for row in rows} == {"renderer_fast_path", "model_per_file"}
    assert all("provider_calls" in row for row in rows)
    assert all("runtime_passed" in row for row in rows)
