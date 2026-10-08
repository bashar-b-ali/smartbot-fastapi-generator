"""Generate runtime-facing project documentation from the current app."""
from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.config import BASE_DIR, Settings, settings
from app.core.logging import logger

API_ENDPOINTS_PATH = BASE_DIR / "API_ENDPOINTS.md"
PROJECT_REQUIREMENTS_PATH = BASE_DIR / "PROJECT_REQUIREMENTS.md"


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _schema_name(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "Any"
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " | ".join(_schema_name(item) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(_schema_name(item) for item in schema["oneOf"])
    if "allOf" in schema:
        return " & ".join(_schema_name(item) for item in schema["allOf"])
    if schema.get("type") == "array":
        return f"list[{_schema_name(schema.get('items'))}]"
    return str(schema.get("type") or schema.get("title") or "Any")


def _request_body_params(body: dict[str, Any] | None) -> list[str]:
    if not body:
        return []
    required = "required" if body.get("required") else "optional"
    rendered = []
    for content_type, content in sorted((body.get("content") or {}).items()):
        rendered.append(
            f"`body` `{_md_cell(content_type)}: {_md_cell(_schema_name(content.get('schema')))}` ({required})"
        )
    return rendered


def _operation_parameters(operation: dict[str, Any]) -> str:
    rendered = []
    for param in operation.get("parameters") or []:
        name = param.get("name", "")
        location = param.get("in", "query")
        required = "required" if param.get("required") else "optional"
        schema_name = _schema_name(param.get("schema"))
        rendered.append(
            f"`{_md_cell(location)}` `{_md_cell(name)}: {_md_cell(schema_name)}` ({required})"
        )
    rendered.extend(_request_body_params(operation.get("requestBody")))
    return "<br>".join(rendered) if rendered else "-"


def _operation_summary(method: str, path: str, operation: dict[str, Any]) -> str:
    summary = operation.get("summary") or operation.get("description")
    if summary:
        return str(summary).splitlines()[0][:240]
    if method == "GET" and "{" not in path:
        return "Lists resources or returns application state."
    if method == "GET":
        return "Returns a resource by identifier or query parameters."
    if method == "POST":
        return "Creates a resource, uploads data, or runs an action."
    if method in {"PUT", "PATCH"}:
        return "Updates an existing resource."
    if method == "DELETE":
        return "Deletes an existing resource."
    return "API operation."


def render_api_endpoints(app: FastAPI) -> str:
    schema = app.openapi()
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for path, operations in sorted((schema.get("paths") or {}).items()):
        for method, operation in sorted(operations.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            rows.append((method.upper(), path, operation))

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        "# API Endpoints",
        "",
        f"Generated from the live FastAPI OpenAPI schema at `{generated_at}`.",
        "",
        "| Method | Path | Parameters | Response Models | What it does |",
        "| --- | --- | --- | --- | --- |",
    ]
    for method, path, operation in rows:
        responses = operation.get("responses") or {}
        response_models = []
        for status, response in sorted(responses.items()):
            content = response.get("content") or {}
            models = sorted({_schema_name(item.get("schema")) for item in content.values()})
            model_text = ", ".join(models) if models else response.get("description", "")
            response_models.append(f"`{_md_cell(status)}` {_md_cell(model_text or '-')}")
        lines.append(
            f"| `{method}` | `{_md_cell(path)}` | {_operation_parameters(operation)} | "
            f"{'<br>'.join(response_models) or '-'} | {_md_cell(_operation_summary(method, path, operation))} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _project_dependencies() -> list[str]:
    pyproject = BASE_DIR / "pyproject.toml"
    if not pyproject.exists():
        return []
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    return list((data.get("project") or {}).get("dependencies") or [])


def _settings_fields() -> list[tuple[str, Any, bool]]:
    fields = []
    for name, field in Settings.model_fields.items():
        env_name = name.upper()
        default = field.default
        required = field.is_required()
        if name in {"secret_key", "db_password", "smtp_password", "openai_api_key", "anthropic_api_key", "google_api_key"}:
            default_text: Any = "<secret>"
        elif required or default is None:
            default_text = ""
        else:
            default_text = default
        fields.append((env_name, default_text, required))
    return fields


def render_project_requirements() -> str:
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    deps = _project_dependencies()
    lines = [
        "# Project Requirements",
        "",
        f"Generated from backend configuration and `pyproject.toml` at `{generated_at}`.",
        "",
        "## Runtime",
        "",
        "- Python 3.11 or newer.",
        "- MySQL-compatible database reachable by the configured `DB_*` settings.",
        "- Ollama at `OLLAMA_HOST` when using the server default local model.",
        "- Redis at `REDIS_URL` if realtime or queue-backed features are enabled.",
        "- Node.js/npm for the sibling React frontend in `../front-end-final`.",
        "",
        "## Python Dependencies",
        "",
    ]
    if deps:
        lines.extend(f"- `{dep}`" for dep in deps)
    else:
        lines.append("- No dependency metadata found.")

    lines.extend([
        "",
        "## Environment Variables",
        "",
        "| Name | Required | Default / Notes |",
        "| --- | --- | --- |",
    ])
    for env_name, default, required in _settings_fields():
        lines.append(
            f"| `{_md_cell(env_name)}` | {'yes' if required else 'no'} | `{_md_cell(default)}` |"
        )

    lines.extend([
        "",
        "## Generated Project Data",
        "",
        f"- Project files live under `{_md_cell(settings.projects_root)}`.",
        "- Each generated project stores a compact SQL/project index in `project_helpers`.",
        "- Each generated project receives its own `API_ENDPOINTS.md` with methods, paths, request parameters, handlers, and SQLModel tables.",
        "- Root docs for this backend are exposed at `/api/v1/docs/api-endpoints` and `/api/v1/docs/project-requirements`.",
        "",
        "## Startup Checklist",
        "",
        "- Apply migrations with `alembic upgrade head`.",
        "- Start Ollama and create or install the selected local model manually, for example `ollama create fastAPI_Model -f Modelfile.fastAPI_Model`; the backend never pulls models automatically.",
        "- Start the backend with Uvicorn and the frontend with npm.",
        "- Keep `SECRET_KEY` stable; it is used for JWTs and encrypted stored user API keys.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def write_runtime_docs(app: FastAPI) -> dict[str, Path]:
    docs = {
        "api_endpoints": (API_ENDPOINTS_PATH, render_api_endpoints(app)),
        "project_requirements": (PROJECT_REQUIREMENTS_PATH, render_project_requirements()),
    }
    written: dict[str, Path] = {}
    for key, (path, content) in docs.items():
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != content:
            path.write_text(content, encoding="utf-8", newline="\n")
        written[key] = path
    logger.info("runtime.docs refreshed", files={key: str(path) for key, path in written.items()})
    return written
