from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.llm.file_spec import FileSpec
from app.services.model_pipeline.artifacts import (
    canonical_path as _normalized_canonical_path,
)
from app.services.model_pipeline.artifacts import (
    normalize_artifact_contract,
)
from app.services.model_pipeline.capabilities import (
    capability_for_quest_kind,
    owner_for_capability,
)
from app.services.model_pipeline.naming import (
    class_name as _normalized_class_name,
)
from app.services.model_pipeline.naming import (
    identifier as _normalized_identifier,
)
from app.services.model_pipeline.profiles import FASTAPI_PROFILE
from app.services.model_pipeline.validation import validate_file_specs_batch


@dataclass(frozen=True)
class Quest:
    id: str
    kind: str
    objective: str
    artifact_contract: dict[str, Any]
    owner: str = "renderer"
    capability_id: str = "crud_resource"
    target_symbols: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    validation_scope: list[str] = field(default_factory=list)
    max_tokens: int = 1600

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactDelta:
    existing: list[str] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)
    candidate_added: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def quest_dicts(quests: list[Quest]) -> list[dict[str, Any]]:
    return [quest.as_dict() for quest in quests]


def _quest(
    *,
    id: str,
    kind: str,
    objective: str,
    artifact_contract: dict[str, Any],
    target_symbols: list[str] | None = None,
    target_files: list[str] | None = None,
    depends_on: list[str] | None = None,
    validation_scope: list[str] | None = None,
    max_tokens: int = 1600,
) -> Quest:
    probe = {"artifact_contract": artifact_contract, "target_symbols": target_symbols or []}
    capability_id = capability_for_quest_kind(kind, probe)
    return Quest(
        id=id,
        kind=kind,
        objective=objective,
        artifact_contract=artifact_contract,
        owner=owner_for_capability(capability_id),
        capability_id=capability_id,
        target_symbols=target_symbols or [],
        target_files=target_files or [],
        depends_on=depends_on or [],
        validation_scope=validation_scope or [],
        max_tokens=max_tokens,
    )


def build_generation_quests(canonical_spec: dict[str, Any], artifact_contract: dict[str, Any]) -> list[Quest]:
    contract = normalize_artifact_contract(artifact_contract)
    quests: list[Quest] = []

    for item in contract.get("required_behaviors") or []:
        if not isinstance(item, dict):
            continue
        behavior = _identifier(item.get("behavior") or item.get("kind") or item.get("name"))
        if not behavior:
            continue
        helper_behavior = behavior not in {"pagination", "paginated", "top_n", "top", "ranking"}
        path = _behavior_file(behavior) if helper_behavior else ""
        quests.append(
            _quest(
                id=f"behavior:{behavior}",
                kind="behavior",
                objective=f"Add {behavior} helper behavior.",
                artifact_contract={"required_behaviors": [item]} if helper_behavior else {},
                target_symbols=[behavior],
                target_files=[path] if path else [],
                validation_scope=[path] if path else [],
                max_tokens=2200,
            )
        )

    table_ids: dict[str, str] = {}
    for item in contract.get("required_tables") or []:
        if not isinstance(item, dict):
            continue
        table = _class_name(item.get("table") or item.get("name"))
        if not table:
            continue
        quest_id = f"table:{_identifier(table)}"
        table_ids[_identifier(table)] = quest_id
        quests.append(
            _quest(
                id=quest_id,
                kind="table",
                objective=f"Create or extend SQLModel table {table}.",
                artifact_contract={"required_tables": [item]},
                target_symbols=[table],
                target_files=["models.py"],
                validation_scope=["models.py"],
                max_tokens=2400,
            )
        )

    for item in contract.get("required_fields") or []:
        if not isinstance(item, dict):
            continue
        table = _class_name(item.get("table") or "")
        field_name = _identifier(item.get("field") or item.get("name"))
        if not table or not field_name:
            continue
        table_key = _identifier(table)
        quests.append(
            _quest(
                id=f"field:{table_key}.{field_name}",
                kind="field",
                objective=f"Insert field {field_name} into {table}.",
                artifact_contract={"required_fields": [item]},
                target_symbols=[table, field_name],
                target_files=["models.py"],
                depends_on=[table_ids.get(table_key, f"table:{table_key}")],
                validation_scope=["models.py"],
                max_tokens=1600,
            )
        )

    schema_tables = {_identifier(item.get("table") or item.get("name")) for item in contract.get("required_tables") or [] if isinstance(item, dict)}
    route_tables = {_resource_from_route(item.get("path")) for item in contract.get("required_routes") or [] if isinstance(item, dict)}
    for table_key in sorted((schema_tables | route_tables) - {""}):
        table_name = _class_name(table_key)
        quests.append(
            _quest(
                id=f"schema:{table_key}",
                kind="schema",
                objective=f"Add request/read/update schemas for {table_name} only when endpoints need them.",
                artifact_contract={},
                target_symbols=[table_name],
                target_files=["schemas.py"],
                depends_on=[table_ids.get(table_key, f"table:{table_key}")],
                validation_scope=["schemas.py"],
                max_tokens=1800,
            )
        )

    router_ids: dict[str, str] = {}
    for item in contract.get("required_routes") or []:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or "*").upper()
        path = _canonical_path(item.get("path"))
        resource = _resource_from_route(path) or "root"
        router_id = f"router:{resource}"
        router_ids.setdefault(resource, router_id)
        target_file = "main.py" if resource == "root" else f"routers/{resource}.py"
        quests.append(
            _quest(
                id=f"route:{method}:{path}",
                kind="route",
                objective=f"Add exactly one endpoint function for {method} {path}.",
                artifact_contract={"required_routes": [item]},
                target_symbols=[method, path],
                target_files=[target_file],
                depends_on=[f"schema:{resource}"] if resource != "root" else [],
                validation_scope=[target_file],
                max_tokens=2200,
            )
        )

    for resource, router_id in router_ids.items():
        if resource == "root":
            continue
        route_deps = [
            quest.id
            for quest in quests
            if quest.kind == "route" and any(f"/{resource}" in symbol for symbol in quest.target_symbols)
        ]
        quests.append(
            _quest(
                id=router_id,
                kind="router",
                objective=f"Ensure router file for {resource} exists and exports router.",
                artifact_contract={},
                target_symbols=["router", resource],
                target_files=[f"routers/{resource}.py"],
                depends_on=route_deps,
                validation_scope=[f"routers/{resource}.py"],
                max_tokens=1000,
            )
        )
        quests.append(
            _quest(
                id=f"wiring:{resource}",
                kind="wiring",
                objective=f"Include {resource} router in main.py.",
                artifact_contract={},
                target_symbols=[resource],
                target_files=["main.py"],
                depends_on=[router_id],
                validation_scope=["main.py"],
                max_tokens=900,
            )
        )

    return _dedupe_quests(_topologically_sorted(quests))


def foundation_file_specs(files: list[FileSpec]) -> list[FileSpec]:
    by_path = {item.path: item for item in files}
    return [
        FileSpec(path="main.py", content="from fastapi import FastAPI\n\napp = FastAPI()\n"),
        by_path.get("database.py")
        or FileSpec(
            path="database.py",
            content='from sqlmodel import SQLModel, Session, create_engine\n\nengine = create_engine("sqlite:///./app.db")\n\n\ndef create_db_and_tables() -> None:\n    SQLModel.metadata.create_all(engine)\n\n\ndef get_session():\n    with Session(engine) as session:\n        yield session\n',
        ),
        by_path.get("requirements.txt") or FileSpec(path="requirements.txt", content="fastapi\nuvicorn\nsqlmodel\n"),
        by_path.get("routers/__init__.py") or FileSpec(path="routers/__init__.py", content=""),
    ]


def execute_rendered_quests(files: list[FileSpec], quests: list[Quest], full_contract: dict[str, Any]) -> dict[str, Any]:
    full_contract = normalize_artifact_contract(full_contract)
    by_path = {item.path: item for item in files}
    accepted_paths: list[str] = []
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    accepted_paths.extend(item.path for item in foundation_file_specs(files))
    validations = validate_file_specs_batch(
        files,
        [quest.artifact_contract for quest in quests] + [full_contract],
    )

    for quest, validation in zip(quests, validations[:-1], strict=True):
        quest_files = [by_path[path] for path in quest.target_files if path in by_path]
        delta = ArtifactDelta(
            requested=_flatten_contract(quest.artifact_contract),
            candidate_added=(validation.get("artifact_validation") or {}).get("covered_artifacts") or [],
            missing=(validation.get("artifact_validation") or {}).get("missing_artifacts") or [],
            regressions=(validation.get("artifact_validation") or {}).get("regressions") or [],
        )
        record = {
            **quest.as_dict(),
            "changed_files": [spec.path for spec in quest_files],
            "artifact_delta": delta.as_dict(),
            "validation": {
                "accepted": validation.get("accepted"),
                "static_safe": validation.get("static_safe"),
                "failure_category": validation.get("failure_category"),
            },
        }
        missing = _quest_missing_artifacts(quest, delta.missing)
        if validation.get("static_safe") and not missing and not delta.regressions:
            completed.append(record)
            accepted_paths.extend(spec.path for spec in quest_files if spec.path not in accepted_paths)
        else:
            failed.append({**record, "artifact_delta": {**delta.as_dict(), "missing": missing}})
            break

    final_validation = validations[-1]
    return {
        "completed": completed,
        "failed": failed,
        "missing_artifacts": (final_validation.get("artifact_validation") or {}).get("missing_artifacts") or [],
        "regressions": (final_validation.get("artifact_validation") or {}).get("regressions") or [],
        "files_that_would_change": [item.path for item in files],
        "accepted_paths": accepted_paths,
        "final_validation": final_validation,
        "context_budget": selected_context_budget(quests),
    }


def build_quest_run_report(
    *,
    quests: list[Quest],
    quest_results: dict[str, Any],
    usage: Any,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    completed = [item for item in quest_results.get("completed") or [] if isinstance(item, dict)]
    failed = [item for item in quest_results.get("failed") or [] if isinstance(item, dict)]
    return {
        "quest_count": len(quests),
        "completed_quests": [item.get("id") for item in completed if item.get("id")],
        "failed_quests": [item.get("id") for item in failed if item.get("id")],
        "artifact_deltas": [
            {"quest_id": item.get("id"), **(item.get("artifact_delta") or {})}
            for item in [*completed, *failed]
        ],
        "token_usage": {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "retries": int(getattr(usage, "retries", 0) or 0),
        },
        "changed_files": changed_files or [],
        "files_that_would_change": quest_results.get("files_that_would_change") or [],
        "missing_artifacts": quest_results.get("missing_artifacts") or [],
        "regressions": quest_results.get("regressions") or [],
        "context_budget": quest_results.get("context_budget") or {},
    }


def selected_context_budget(quests: list[Quest], whole_project_chars: int = 0) -> dict[str, Any]:
    selected = sum(max(200, quest.max_tokens * 4) for quest in quests)
    return {
        "selected_context_chars": selected,
        "whole_project_context_chars": whole_project_chars,
        "quest_count": len(quests),
    }


def _merge_file_specs(existing: list[FileSpec], additions: list[FileSpec]) -> list[FileSpec]:
    by_path = {item.path: item for item in existing}
    order = [item.path for item in existing]
    for item in additions:
        if item.path not in by_path:
            order.append(item.path)
        by_path[item.path] = item
    return [by_path[path] for path in order]


def _quest_missing_artifacts(quest: Quest, missing: list[str]) -> list[str]:
    if quest.kind == "route":
        return [item for item in missing if " is not wired in " not in item]
    return missing


def _flatten_contract(contract: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for key, values in contract.items():
        for value in values if isinstance(values, list) else []:
            items.append(f"{key}:{value}")
    return items


def _dedupe_quests(quests: list[Quest]) -> list[Quest]:
    result: list[Quest] = []
    seen: set[str] = set()
    for quest in quests:
        if quest.id in seen:
            continue
        result.append(quest)
        seen.add(quest.id)
    return result


def _topologically_sorted(quests: list[Quest]) -> list[Quest]:
    rank = {"behavior": 0, "table": 1, "field": 2, "schema": 3, "route": 4, "router": 5, "wiring": 6}
    return sorted(quests, key=lambda quest: (rank.get(quest.kind, 99), quest.id))


def _behavior_file(behavior: str) -> str:
    if "websocket" in behavior:
        return "websockets.py"
    if "upload" in behavior or "file" in behavior:
        return "files.py"
    if "auth" in behavior or "jwt" in behavior:
        return "auth.py"
    return FASTAPI_PROFILE.entrypoint_file


def _resource_from_route(path: Any) -> str:
    value = _canonical_path(path)
    first = value.strip("/").split("/", 1)[0]
    return _identifier(first)


def _canonical_path(path: Any) -> str:
    return _normalized_canonical_path(path)


def _identifier(value: Any) -> str:
    return _normalized_identifier(value, numeric_prefix="field")


def _class_name(value: Any) -> str:
    return _normalized_class_name(value)




