from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from time import perf_counter
from typing import Any

from app.llm.providers import LLMResponse, Message
from app.services.model_pipeline.generation import ModelOwnedGenerationPipeline


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.model = "scripted-ablation"
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Message], temperature: float = 0.7, max_tokens: int = 4096, **kwargs) -> LLMResponse:
        self.calls.append({"temperature": temperature, "max_tokens": max_tokens, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("No scripted provider response left")
        return LLMResponse(content=self.responses.pop(0), model=self.model, input_tokens=10, output_tokens=20)

    def stream(self, messages: list[Message], temperature: float = 0.7, max_tokens: int = 4096, **kwargs) -> Iterator[str]:
        yield self.complete(messages, temperature=temperature, max_tokens=max_tokens, **kwargs).content


def _schema(tables: list[dict[str, Any]] | None = None) -> str:
    tables = tables or []
    return json.dumps({
        "domain": "ablation_service",
        "ddl_sql": " ".join(f"CREATE TABLE {table['name']} (id INTEGER PRIMARY KEY);" for table in tables),
        "schema": {"tables": tables, "no_database_required": not tables},
        "no_database_required": not tables,
        "assumptions": [],
    })


def _api(route: str = "/ping", table: str | None = None) -> str:
    contract: dict[str, list[dict[str, Any]]] = {
        "required_routes": [{"requirement_id": "R1", "method": "GET", "path": route}],
    }
    if table:
        contract["required_tables"] = [{"requirement_id": "R1", "table": table}]
    return json.dumps({
        "project_name": "ablation_service",
        "summary": "Ablation fixture API.",
        "requirements": [{"id": "R1", "description": f"Expose GET {route}.", "critical": True}],
        "artifact_contract": contract,
        "assumptions": [],
    })


def _file_plan(paths: list[str]) -> str:
    return json.dumps({"files": [{"path": path, "purpose": "Ablation fixture file.", "depends_on": []} for path in paths], "notes": []})


def _wrapped(path: str, content: str) -> str:
    return f"\n### FILE: {path} ###\n{content.rstrip()}\n### END FILE ###\n"


MAIN = _wrapped("main.py", 'from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get("/ping")\ndef ping():\n    return {"status": "ok"}\n')
DB = _wrapped("database.py", 'from sqlmodel import Session, create_engine\n\nengine = create_engine("sqlite:///test.db")\n\ndef get_session():\n    with Session(engine) as session:\n        yield session\n')
REQ = _wrapped("requirements.txt", "fastapi\nsqlmodel\n")


def _case(name: str, prompt: str, responses: list[str]) -> dict[str, Any]:
    provider = ScriptedProvider(responses)
    started = perf_counter()
    result = ModelOwnedGenerationPipeline(provider).build(prompt)
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    validation = result.stage_outputs.get("validation") or {}
    runtime = validation.get("runtime_validation") or {}
    return {
        "name": name,
        "accepted": bool(validation.get("accepted")),
        "strategy": result.stage_outputs.get("generation_strategy"),
        "provider_calls": len(provider.calls),
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "retries": result.usage.retries,
        "elapsed_ms": elapsed_ms,
        "file_count": len(result.files),
        "runtime_passed": runtime.get("passed"),
        "missing_artifacts": (validation.get("artifact_validation") or {}).get("missing_artifacts") or [],
        "failure_category": validation.get("failure_category"),
    }


def run() -> list[dict[str, Any]]:
    return [
        _case(
            "renderer_fast_path",
            "Build a FastAPI resources API with GET /resources.",
            [_schema(tables=[{"name": "resources", "columns": [{"name": "id"}]}]), _api("/resources", table="Resource")],
        ),
        _case(
            "model_per_file",
            "Build a FastAPI status API with GET /ping and project files.",
            [_schema(), _api("/ping"), _file_plan(["main.py", "database.py", "requirements.txt"]), MAIN, DB, REQ],
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic local pipeline ablation fixtures.")
    parser.add_argument("--markdown", action="store_true", help="Render a compact Markdown table instead of JSON.")
    args = parser.parse_args()
    rows = run()
    if args.markdown:
        print("| Case | Accepted | Strategy | Calls | Tokens In/Out | Retries | Runtime | ms |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in rows:
            print(f"| {row['name']} | {row['accepted']} | {row['strategy']} | {row['provider_calls']} | {row['input_tokens']}/{row['output_tokens']} | {row['retries']} | {row['runtime_passed']} | {row['elapsed_ms']} |")
    else:
        print(json.dumps({"cases": rows}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

