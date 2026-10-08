from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from app.llm.providers import LLMResponse, Message


class ScriptedProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.model = "scripted"
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        if not self.responses:
            raise AssertionError("No scripted provider response left")
        return LLMResponse(
            content=self.responses.pop(0),
            model=self.model,
            input_tokens=10,
            output_tokens=20,
        )

    def stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> Iterator[str]:
        yield self.complete(messages, temperature=temperature, max_tokens=max_tokens, **kwargs).content


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def minimal_project(root: Path, *, route: str = "/ping", status: str = "ok") -> None:
    write(
        root / "main.py",
        f"""
from fastapi import FastAPI

app = FastAPI()


@app.get("{route}")
def endpoint():
    return {{"status": "{status}"}}
""".lstrip(),
    )
    write(
        root / "database.py",
        """
from sqlmodel import Session, create_engine

engine = create_engine("sqlite:///test.db")


def get_session():
    with Session(engine) as session:
        yield session
""".lstrip(),
    )
    write(root / "requirements.txt", "fastapi\nsqlmodel\n")


def schema_json(*, domain: str = "generic_service", tables: list[dict] | None = None) -> str:
    no_database = not tables
    return json.dumps(
        {
            "domain": domain,
            "ddl_sql": "" if no_database else " ".join(
                f"CREATE TABLE {table['name']} (id INTEGER PRIMARY KEY);" for table in tables or []
            ),
            "schema": {"tables": tables or [], "no_database_required": no_database},
            "no_database_required": no_database,
            "assumptions": [],
        }
    )


def api_contract_json(
    *,
    route: str = "/ping",
    method: str = "GET",
    table: str | None = None,
    field: str | None = None,
) -> str:
    artifact_contract: dict[str, list[dict]] = {
        "required_routes": [{"requirement_id": "R1", "method": method, "path": route}]
    }
    if table:
        artifact_contract.setdefault("required_tables", []).append({"requirement_id": "R1", "table": table})
    if table and field:
        artifact_contract.setdefault("required_fields", []).append(
            {"requirement_id": "R1", "table": table, "field": field}
        )
    return json.dumps(
        {
            "project_name": "generic_service",
            "summary": "Generic model-owned API.",
            "requirements": [{"id": "R1", "description": f"Expose {method} {route}.", "critical": True}],
            "artifact_contract": artifact_contract,
            "assumptions": [],
        }
    )


def file_plan_json(paths: list[str] | None = None) -> str:
    return json.dumps(
        {
            "files": [
                {"path": path, "purpose": "Model-selected project file.", "depends_on": []}
                for path in (paths or ["main.py", "database.py", "requirements.txt"])
            ],
            "notes": [],
        }
    )


def wrapped_file(path: str, content: str) -> str:
    return f"\n### FILE: {path} ###\n{content.rstrip()}\n### END FILE ###\n"


def main_file(route: str = "/ping", status: str = "ok") -> str:
    return wrapped_file(
        "main.py",
        f"""
from fastapi import FastAPI

app = FastAPI()


@app.get("{route}")
def endpoint():
    return {{"status": "{status}"}}
""".lstrip(),
    )


def database_file() -> str:
    return wrapped_file(
        "database.py",
        """
from sqlmodel import Session, create_engine

engine = create_engine("sqlite:///test.db")


def get_session():
    with Session(engine) as session:
        yield session
""".lstrip(),
    )


def requirements_file() -> str:
    return wrapped_file("requirements.txt", "fastapi\nsqlmodel\n")


def no_schema_delta_json() -> str:
    return json.dumps(
        {
            "summary": "No database schema change.",
            "no_schema_change": True,
            "ddl_sql": "",
            "schema": {"tables": [], "no_database_required": True},
            "affected_tables": [],
            "affected_fields": [],
            "assumptions": [],
        }
    )


def edit_contract_json(route: str = "/ping") -> str:
    return json.dumps(
        {
            "summary": "Generic route edit contract.",
            "requirements": [{"id": "R1", "description": f"{route} remains available.", "critical": True}],
            "artifact_contract": {
                "required_routes": [{"requirement_id": "R1", "method": "GET", "path": route}]
            },
            "target_files": ["main.py"],
            "missing_info": [],
        }
    )
