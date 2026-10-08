from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.llm.file_spec import FileSpec


@dataclass(frozen=True)
class RenderedProject:
    files: list[FileSpec]
    render_plan: dict[str, Any]


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    py_type: str
    nullable: bool = True
    default: Any = None
    primary_key: bool = False
    unique: bool = False
    indexed: bool = False
    foreign_key: str | None = None


@dataclass(frozen=True)
class ResourceSpec:
    table_name: str
    class_name: str
    route_name: str
    router_var: str
    columns: list[ColumnSpec]
    filter_fields: list[str]
    actions: set[str]


def render_fastapi_sqlmodel_project(
    *,
    prompt: str,
    schema_plan: dict[str, Any],
    api_contract: dict[str, Any],
    file_plan: dict[str, Any] | None = None,
) -> RenderedProject | None:
    resources = _resources_from_schema(schema_plan, api_contract)
    if not resources:
        return None
    capabilities = _capabilities(prompt, api_contract)
    access_rules = _access_rules(api_contract)
    auth_resource = _prepare_auth_resource(resources, access_rules=access_rules) if capabilities["auth"] else None
    if capabilities["auth"] and auth_resource is None:
        return None
    _prepare_access_resources(resources, access_rules)
    custom_routes = _custom_routes(api_contract)
    api_prefix = _api_prefix(api_contract, resources)
    files = [
        FileSpec(path="database.py", content=_render_database()),
        FileSpec(path="models.py", content=_render_models(resources)),
        FileSpec(path="schemas.py", content=_render_schemas(resources, auth_resource=auth_resource)),
        FileSpec(path="routers/__init__.py", content=""),
    ]
    if auth_resource is not None:
        files.append(FileSpec(path="auth.py", content=_render_auth(auth_resource, api_prefix=api_prefix)))
    if capabilities["files"]:
        files.append(
            FileSpec(path="files.py", content=_render_files_router(capabilities["auth"], api_prefix=api_prefix))
        )
    if capabilities["websocket"]:
        files.append(FileSpec(path="websockets.py", content=_render_websocket_router()))
    for resource in resources:
        files.append(
            FileSpec(
                path=f"routers/{resource.route_name}.py",
                content=_render_resource_router(
                    resource,
                    resources,
                    capabilities,
                    custom_routes,
                    auth_resource=auth_resource,
                    access_rules=access_rules,
                    api_prefix=api_prefix,
                ),
            )
        )
    files.append(FileSpec(path="main.py", content=_render_main(resources, capabilities, auth_resource=auth_resource)))
    files.append(FileSpec(path="requirements.txt", content=_render_requirements(capabilities)))
    planned_paths = [
        str(item.get("path"))
        for item in (file_plan or {}).get("files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    return RenderedProject(
        files=files,
        render_plan={
            "strategy": "model_spec_sqlmodel_renderer",
            "resources": [
                {
                    "table": resource.table_name,
                    "class": resource.class_name,
                    "router": f"routers/{resource.route_name}.py",
                    "fields": [column.name for column in resource.columns],
                    "filters": resource.filter_fields,
                }
                for resource in resources
            ],
            "capabilities": capabilities,
            "api_prefix": api_prefix,
            "custom_routes": custom_routes,
            "planned_paths": planned_paths,
            "rendered_paths": [item.path for item in files],
        },
    )


_ROUTER_ROOT_SEGMENTS = {"auth", "files", "file", "ws", "websocket", "websockets", "token", "login"}


def _declared_route_paths(api_contract: dict[str, Any]) -> list[str]:
    raw_artifact = api_contract.get("artifact_contract")
    artifact = raw_artifact if isinstance(raw_artifact, dict) else {}
    paths: list[str] = []
    for source in (artifact.get("required_routes"), api_contract.get("routes"), artifact.get("custom_routes")):
        for item in source or []:
            if isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
            elif isinstance(item, str) and item.strip():
                paths.append(item)
    return paths


def _api_prefix(api_contract: dict[str, Any], resources: list[ResourceSpec]) -> str:
    """Project-wide route prefix the contract asked for, e.g. `/api`.

    Without this the contract says `/api/books` while the renderer serves
    `/books`, so every declared route reads as missing during validation.
    """
    reserved = {resource.route_name for resource in resources}
    reserved |= {_singular_snake(resource.table_name) for resource in resources}
    reserved |= {_plural_snake(resource.table_name) for resource in resources}
    reserved |= _ROUTER_ROOT_SEGMENTS
    segment_lists: list[list[str]] = []
    for path in _declared_route_paths(api_contract):
        segments = [part for part in _canonical_path(path).strip("/").split("/") if part]
        if not segments:
            return ""
        segment_lists.append(segments)
    if not segment_lists:
        return ""
    common: list[str] = []
    for index in range(min(len(segments) for segments in segment_lists)):
        segment = segment_lists[0][index]
        if segment.startswith("{") or segment.lower() in reserved:
            break
        if any(segments[index] != segment for segments in segment_lists):
            break
        common.append(segment)
    return "/" + "/".join(common) if common else ""


def _resources_from_schema(schema_plan: dict[str, Any], api_contract: dict[str, Any]) -> list[ResourceSpec]:
    schema = schema_plan.get("schema") if isinstance(schema_plan.get("schema"), dict) else {}
    tables = schema.get("tables") if isinstance(schema.get("tables"), list) else []
    resources: list[ResourceSpec] = []
    known_tables = {_plural_snake(str(table.get("name") or table.get("table") or table.get("model") or "")) for table in tables if isinstance(table, dict)}
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = _table_name(table)
        if not table_name:
            continue
        columns = _columns_from_table(table, known_tables=known_tables)
        if not columns:
            columns = [ColumnSpec(name="id", py_type="int", primary_key=True, nullable=True)]
        if not any(column.primary_key or column.name == "id" for column in columns):
            columns = [ColumnSpec(name="id", py_type="int", primary_key=True, nullable=True), *columns]
        class_name = _class_name(table_name)
        resources.append(
            ResourceSpec(
                table_name=table_name,
                class_name=class_name,
                route_name=_plural_snake(table_name),
                router_var=f"{_singular_snake(table_name)}_router",
                columns=columns,
                filter_fields=_filter_fields(table, api_contract, table_name, class_name),
                actions=_actions_for_resource(api_contract, table_name),
            )
        )
    return resources


def _prepare_auth_resource(
    resources: list[ResourceSpec],
    *,
    access_rules: list[dict[str, Any]] | None = None,
) -> ResourceSpec | None:
    auth_resource = next(
        (
            resource
            for resource in resources
            if _singular_snake(resource.table_name) in {"user", "account"}
        ),
        None,
    )
    if auth_resource is None:
        auth_resource = ResourceSpec(
            table_name="users",
            class_name="User",
            route_name="users",
            router_var="user_router",
            columns=[ColumnSpec(name="id", py_type="int", primary_key=True, nullable=True)],
            filter_fields=[],
            actions={"create", "list", "detail", "update", "delete"},
        )
        resources.append(auth_resource)
    existing = {column.name for column in auth_resource.columns}
    if "username" not in existing:
        auth_resource.columns.append(
            ColumnSpec(name="username", py_type="str", nullable=False, unique=True, indexed=True)
        )
    if not {"password_hash", "hashed_password"} & existing:
        auth_resource.columns.append(ColumnSpec(name="password_hash", py_type="str", nullable=False))
    if access_rules and "role" not in existing:
        auth_resource.columns.append(ColumnSpec(name="role", py_type="str", nullable=False, default="user"))
    return auth_resource


def _password_hash_field(resource: ResourceSpec) -> str:
    names = {column.name for column in resource.columns}
    return "hashed_password" if "hashed_password" in names else "password_hash"


def _prepare_access_resources(
    resources: list[ResourceSpec],
    access_rules: list[dict[str, Any]],
) -> None:
    auth_resource = next(
        (
            candidate
            for candidate in resources
            if _singular_snake(candidate.table_name) in {"user", "account"}
        ),
        None,
    )
    for resource in resources:
        route_prefix = f"/{resource.route_name}"
        scoped_roles: dict[str, str] = {}
        for method in ("POST", "GET"):
            scope_rule = _matching_access_rule(
                access_rules,
                resource,
                method,
                route_prefix,
                "current_user_scoped",
            )
            role_rule = _matching_access_rule(
                access_rules,
                resource,
                method,
                route_prefix,
                "role_restricted",
            )
            role = _safe_identifier(str((role_rule or {}).get("role") or ""))
            if scope_rule is not None and role:
                scoped_roles[method] = role
                identity_resource = next(
                    (
                        candidate
                        for candidate in resources
                        if _singular_snake(candidate.table_name) == role
                    ),
                    None,
                )
                if (
                    identity_resource is not None
                    and auth_resource is not None
                    and "user_id" not in {column.name for column in identity_resource.columns}
                ):
                    identity_resource.columns.append(
                        ColumnSpec(
                            name="user_id",
                            py_type="int",
                            nullable=False,
                            indexed=True,
                            foreign_key=f"{auth_resource.table_name}.id",
                        )
                    )
        role = scoped_roles.get("POST")
        if not role or "course_id" not in {column.name for column in resource.columns}:
            continue
        course = next(
            (
                candidate
                for candidate in resources
                if _singular_snake(candidate.table_name) == "course"
            ),
            None,
        )
        if course is None:
            continue
        owner_field = f"{role}_id"
        if owner_field in {column.name for column in course.columns}:
            continue
        owner_resource = next(
            (
                candidate
                for candidate in resources
                if _singular_snake(candidate.table_name) == role
            ),
            None,
        )
        course.columns.append(
            ColumnSpec(
                name=owner_field,
                py_type="int",
                nullable=False,
                indexed=True,
                foreign_key=(f"{owner_resource.table_name}.id" if owner_resource is not None else None),
            )
        )


def _access_rules(api_contract: dict[str, Any]) -> list[dict[str, Any]]:
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else api_contract
    return [
        dict(item)
        for item in contract.get("required_behaviors") or []
        if isinstance(item, dict)
        and _safe_identifier(str(item.get("behavior") or item.get("kind") or ""))
        in {"role_restricted", "current_user_scoped"}
    ]


def _matching_access_rule(
    rules: list[dict[str, Any]],
    resource: ResourceSpec,
    method: str,
    path: str,
    behavior: str,
) -> dict[str, Any] | None:
    resource_names = {
        _singular_snake(resource.table_name),
        _plural_snake(resource.table_name),
        resource.class_name.lower(),
    }
    canonical_path = "/" + "/".join(part for part in path.strip().lower().split("/") if part)
    for rule in rules:
        if _safe_identifier(str(rule.get("behavior") or rule.get("kind") or "")) != behavior:
            continue
        rule_table = _singular_snake(str(rule.get("table") or ""))
        if rule_table and rule_table not in resource_names:
            continue
        rule_method = str(rule.get("method") or "").upper()
        if rule_method and rule_method != method.upper():
            continue
        rule_path = "/" + "/".join(part for part in str(rule.get("path") or "").strip().lower().split("/") if part)
        # Declared rule paths may carry the project-wide API prefix while the
        # renderer matches on the router-relative path.
        if rule.get("path") and rule_path != canonical_path and not rule_path.endswith(canonical_path):
            continue
        return rule
    return None


def _role_guard(rule: dict[str, Any] | None) -> str:
    if not rule:
        return ""
    role = _safe_identifier(str(rule.get("role") or ""))
    if not role:
        return ""
    return f'    require_role(current_user, "{role}")\n'


def _scope_filter(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    rule: dict[str, Any] | None,
) -> tuple[str, list[ResourceSpec]]:
    if not rule:
        return "", []
    column_names = {column.name for column in resource.columns}
    for field in ("assignee_id", "student_id", "user_id", "owner_id"):
        if field in column_names:
            identity = _identity_resource(resources, field)
            if identity is not None:
                return (
                    f"    statement = statement.join({identity.class_name}, "
                    f"{identity.class_name}.id == {resource.class_name}.{field})\n"
                    f"    statement = statement.where({identity.class_name}.user_id == current_user.id)\n",
                    [identity],
                )
            return f"    statement = statement.where({resource.class_name}.{field} == current_user.id)\n", []
    if "course_id" not in column_names:
        return "", []
    enrollment = next(
        (
            candidate
            for candidate in resources
            if _singular_snake(candidate.table_name) == "enrollment"
            and {"student_id", "course_id"} <= {column.name for column in candidate.columns}
        ),
        None,
    )
    if enrollment is None:
        return "", []
    student = _identity_resource(resources, "student_id")
    if student is not None:
        return (
            f"    statement = statement.join({enrollment.class_name}, "
            f"{enrollment.class_name}.course_id == {resource.class_name}.course_id)\n"
            f"    statement = statement.join({student.class_name}, "
            f"{student.class_name}.id == {enrollment.class_name}.student_id)\n"
            f"    statement = statement.where({student.class_name}.user_id == current_user.id)\n",
            [enrollment, student],
        )
    return (
        f"    statement = statement.join({enrollment.class_name}, "
        f"{enrollment.class_name}.course_id == {resource.class_name}.course_id)\n"
        f"    statement = statement.where({enrollment.class_name}.student_id == current_user.id)\n",
        [enrollment],
    )


def _identity_resource(
    resources: list[ResourceSpec],
    foreign_key_field: str,
) -> ResourceSpec | None:
    identity_name = _singular_snake(foreign_key_field.removesuffix("_id"))
    return next(
        (
            candidate
            for candidate in resources
            if _singular_snake(candidate.table_name) == identity_name
            and "user_id" in {column.name for column in candidate.columns}
        ),
        None,
    )


def _create_scope_guard(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    scope_rule: dict[str, Any] | None,
    role_rule: dict[str, Any] | None,
) -> tuple[str, list[ResourceSpec]]:
    if not scope_rule or "course_id" not in {column.name for column in resource.columns}:
        return "", []
    course = next(
        (
            candidate
            for candidate in resources
            if _singular_snake(candidate.table_name) == "course"
        ),
        None,
    )
    if course is None:
        return "", []
    course_fields = {column.name for column in course.columns}
    role = _safe_identifier(str((role_rule or {}).get("role") or ""))
    ownership_fields = [f"{role}_id"] if role else []
    ownership_fields.extend(["teacher_id", "user_id", "owner_id"])
    ownership_field = next(
        (field for field in dict.fromkeys(ownership_fields) if field in course_fields),
        None,
    )
    if ownership_field is None:
        return "", []
    owner_resource = _identity_resource(resources, ownership_field)
    if owner_resource is not None:
        return (
            f"    owned_course = session.exec(select({course.class_name}).join(\n"
            f"        {owner_resource.class_name}, {owner_resource.class_name}.id == {course.class_name}.{ownership_field}\n"
            "    ).where(\n"
            f"        {course.class_name}.id == payload.course_id,\n"
            f"        {owner_resource.class_name}.user_id == current_user.id,\n"
            "    )).first()\n"
            "    if owned_course is None:\n"
            "        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Course is not owned by current user\")\n",
            [course, owner_resource],
        )
    return (
        f"    owned_course = session.exec(select({course.class_name}).where(\n"
        f"        {course.class_name}.id == payload.course_id,\n"
        f"        {course.class_name}.{ownership_field} == current_user.id,\n"
        "    )).first()\n"
        "    if owned_course is None:\n"
        "        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=\"Course is not owned by current user\")\n",
        [course],
    )


def _actions_for_resource(api_contract: dict[str, Any], table_name: str) -> set[str]:
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {}
    actions: set[str] = set()
    base = f"/{_plural_snake(table_name)}"
    has_custom_route = any(
        isinstance(route, dict)
        and str(route.get("parent_table") or route.get("table") or "").strip().lower()
        in {table_name.lower(), _plural_snake(table_name).lower(), _singular_snake(table_name).lower()}
        for route in contract.get("custom_routes") or []
    )
    for route in contract.get("required_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = _canonical_path(route.get("path"))
        if path == base and method == "POST":
            actions.add("create")
        elif path == base and method == "GET":
            actions.add("list")
        elif _is_direct_item_route(path, base) and method == "GET":
            actions.add("detail")
        elif _is_direct_item_route(path, base) and method in {"PUT", "PATCH"}:
            actions.add("update")
        elif _is_direct_item_route(path, base) and method == "DELETE":
            actions.add("delete")
    if actions or has_custom_route:
        return actions
    return {"create", "list", "detail", "update", "delete"}


def _is_direct_item_route(path: str, base: str) -> bool:
    parts = [part for part in _canonical_path(path).strip("/").split("/") if part]
    base_parts = [part for part in _canonical_path(base).strip("/").split("/") if part]
    return len(parts) == len(base_parts) + 1 and parts[: len(base_parts)] == base_parts and parts[-1].startswith("{")


def _table_name(table: dict[str, Any]) -> str:
    return str(table.get("name") or table.get("table") or table.get("model") or "").strip()


def _columns_from_table(table: dict[str, Any], *, known_tables: set[str] | None = None) -> list[ColumnSpec]:
    columns: list[ColumnSpec] = []
    for raw_column in table.get("columns") or table.get("fields") or []:
        if not isinstance(raw_column, dict):
            name = _safe_identifier(str(raw_column))
            raw_column = {"name": name}
        name = _safe_identifier(str(raw_column.get("name") or raw_column.get("field") or raw_column.get("column") or ""))
        if not name:
            continue
        reference = (
            raw_column.get("references")
            or raw_column.get("foreign_key")
            or _relationship_reference_for(table, name)
            or _infer_foreign_key(name, known_tables or set())
        )
        columns.append(
            ColumnSpec(
                name=name,
                py_type=_python_type(raw_column.get("type") or raw_column.get("kind") or raw_column.get("python_type")),
                nullable=bool(raw_column.get("nullable", name != "id")),
                default=raw_column.get("default"),
                primary_key=bool(raw_column.get("primary_key") or raw_column.get("pk") or name == "id"),
                unique=bool(raw_column.get("unique")),
                indexed=bool(
                    raw_column.get("index")
                    or raw_column.get("indexed")
                    or raw_column.get("filter")
                    or raw_column.get("searchable")
                    or name.endswith("_id")
                ),
                foreign_key=_foreign_key(reference),
            )
        )
    return columns


def _infer_foreign_key(field_name: str, known_tables: set[str]) -> str | None:
    if not field_name.endswith("_id") or field_name == "id":
        return None
    target = _plural_snake(field_name[:-3])
    return f"{target}.id" if target in known_tables else None


def _relationship_reference_for(table: dict[str, Any], field_name: str) -> str | None:
    target = _safe_identifier(field_name)
    for relationship in table.get("relationships") or table.get("foreign_keys") or []:
        if not isinstance(relationship, dict):
            continue
        column = (
            relationship.get("column")
            or relationship.get("column_name")
            or relationship.get("field")
            or relationship.get("field_name")
            or relationship.get("from_field")
            or relationship.get("from_column")
            or relationship.get("name")
        )
        if not column and isinstance(relationship.get("columns"), list) and relationship["columns"]:
            column = relationship["columns"][0]
        if _safe_identifier(str(column or "")) != target:
            continue
        related_table = (
            relationship.get("related_table")
            or relationship.get("related_table_name")
            or relationship.get("to_table")
            or relationship.get("to_table_name")
            or relationship.get("target_table")
            or relationship.get("target_table_name")
            or relationship.get("references_table")
            or relationship.get("references_table_name")
            or relationship.get("to")
            or relationship.get("table")
        )
        related_column = (
            relationship.get("related_column")
            or relationship.get("related_column_name")
            or relationship.get("to_field")
            or relationship.get("to_column")
            or relationship.get("references_column")
            or relationship.get("references_column_name")
            or "id"
        )
        if related_table:
            return f"{related_table}.{related_column}"
    return None
def _foreign_key(reference: Any) -> str | None:
    if not reference:
        return None
    text = str(reference).strip()
    if not text:
        return None
    if "." in text:
        parts = text.replace("(", ".").replace(")", "").split(".")
        return f"{_plural_snake(parts[0])}.{parts[1] if len(parts) > 1 and parts[1] else 'id'}"
    table = text.split("(", 1)[0].strip()
    return f"{_plural_snake(table)}.id" if table else None


def _filter_fields(table: dict[str, Any], api_contract: dict[str, Any], table_name: str, class_name: str) -> list[str]:
    fields: list[str] = []
    for item in table.get("search_fields") or table.get("filters") or table.get("filter_fields") or []:
        if isinstance(item, dict):
            raw = item.get("columns") or item.get("fields") or item.get("field") or item.get("name")
            values = raw if isinstance(raw, list) else [raw]
        else:
            values = [item]
        for value in values:
            name = _safe_identifier(str(value or ""))
            if name:
                fields.append(name)
    for column in table.get("columns") or table.get("fields") or []:
        if isinstance(column, dict) and any(column.get(key) for key in ("index", "indexed", "filter", "searchable")):
            name = _safe_identifier(str(column.get("name") or column.get("field") or ""))
            if name:
                fields.append(name)
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {}
    table_markers = {
        table_name.lower(),
        _singular_snake(table_name).lower(),
        _plural_snake(table_name).lower(),
        class_name.lower(),
    }
    for item in contract.get("required_filters") or []:
        if not isinstance(item, dict):
            continue
        raw_table = str(item.get("table") or item.get("model") or "").strip().lower()
        if raw_table not in table_markers:
            continue
        field = _safe_identifier(str(item.get("field") or item.get("name") or ""))
        if field:
            fields.append(field)
    return list(dict.fromkeys(fields))


def _capabilities(prompt: str, api_contract: dict[str, Any]) -> dict[str, bool]:
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {}
    auth_policy = api_contract.get("auth_policy") if isinstance(api_contract.get("auth_policy"), dict) else {}
    auth_policy = {**(contract.get("auth_policy") if isinstance(contract.get("auth_policy"), dict) else {}), **auth_policy}
    text = " ".join(
        [
            prompt,
            str(api_contract.get("auth") or ""),
            str(api_contract.get("websocket_events") or ""),
            str(contract.get("required_behaviors") or ""),
        ]
    ).lower()
    public_resources = bool(auth_policy.get("public_resources")) or any(
        term in text for term in ("can be public", "resource endpoints can be public", "public endpoints")
    )
    auth_requested = any(
        term in text
        for term in (
            "auth",
            "jwt",
            "login",
            "protect",
            "protected",
            "admin",
            "current user",
            "current_user",
            "/me/",
        )
    )
    return {
        "auth": auth_requested and not public_resources,
        "public_resources": public_resources,
        "files": bool(auth_policy.get("files")) or any(term in text for term in ("file upload", "upload file", "download file", "multipart", "csv")),
        "websocket": any(term in text for term in ("websocket", "web socket", "ws://")),
    }


def _custom_routes(api_contract: dict[str, Any]) -> list[dict[str, Any]]:
    contract = api_contract.get("artifact_contract") if isinstance(api_contract.get("artifact_contract"), dict) else {}
    routes = contract.get("custom_routes") if isinstance(contract.get("custom_routes"), list) else []
    behaviors = [
        item
        for item in contract.get("required_behaviors") or []
        if isinstance(item, dict)
    ]
    tables = [
        _plural_snake(str(item.get("table") or item.get("name") or ""))
        for item in contract.get("required_tables") or []
        if isinstance(item, dict)
    ]
    fields = [
        item
        for item in contract.get("required_fields") or []
        if isinstance(item, dict)
    ]
    normalized: list[dict[str, Any]] = []
    for raw_route in routes:
        if not isinstance(raw_route, dict):
            continue
        route = dict(raw_route)
        path = _canonical_path(route.get("path"))
        matching = [
            item
            for item in behaviors
            if _canonical_path(item.get("path")) == path
        ]
        download = next(
            (
                item
                for item in matching
                if _safe_identifier(str(item.get("behavior") or item.get("kind") or "")) == "file_download"
            ),
            None,
        )
        if download is not None:
            path_parts = [part for part in path.strip("/").split("/") if part]
            owner_table = _plural_snake(path_parts[0]) if path_parts else ""
            raw_requested_table = str(download.get("table") or "")
            requested_table = _plural_snake(raw_requested_table) if raw_requested_table else ""
            data_table = requested_table or next(
                (
                    table
                    for table in tables
                    if table != owner_table
                    and (_singular_snake(table) in path.lower() or table in path.lower())
                ),
                owner_table,
            )
            route.update(
                {
                    "kind": "file_download",
                    "parent_table": owner_table,
                    "data_table": data_table,
                    "format": str(download.get("format") or "text").lower(),
                    "scoped": any(
                        _safe_identifier(str(item.get("behavior") or item.get("kind") or ""))
                        == "current_user_scoped"
                        for item in matching
                    ),
                    "fields": [
                        _safe_identifier(str(field))
                        for field in download.get("fields") or []
                        if _safe_identifier(str(field))
                    ]
                    or [
                        _safe_identifier(str(item.get("field") or item.get("name") or ""))
                        for item in fields
                        if not item.get("table")
                        or _plural_snake(str(item.get("table"))) == data_table
                    ],
                }
            )
        normalized.append(route)
    return normalized


def _render_database() -> str:
    return """from collections.abc import Generator

from sqlmodel import SQLModel, Session, create_engine


DATABASE_URL = "sqlite:///./app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
"""


def _render_models(resources: list[ResourceSpec]) -> str:
    imports = {"from sqlmodel import Field, SQLModel"}
    if any(column.py_type == "date" for resource in resources for column in resource.columns):
        imports.add("from datetime import date")
    if any(column.py_type == "datetime" for resource in resources for column in resource.columns):
        imports.add("from datetime import datetime")
    blocks = ["\n".join(sorted(imports)), ""]
    for resource in resources:
        lines = [f"class {resource.class_name}(SQLModel, table=True):", f'    __tablename__ = "{resource.table_name}"']
        for column in resource.columns:
            lines.append(f"    {column.name}: {_model_annotation(column)} = {_field_expression(column)}")
        blocks.append("\n".join(lines))
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def _model_annotation(column: ColumnSpec) -> str:
    if column.primary_key or column.nullable:
        return f"{column.py_type} | None"
    return column.py_type


def _field_expression(column: ColumnSpec) -> str:
    args: list[str] = []
    kwargs: list[str] = []
    if column.primary_key:
        kwargs.extend(["default=None", "primary_key=True"])
    elif column.default is not None:
        kwargs.append(f"default={_literal(column.default)}")
    elif column.nullable:
        kwargs.append("default=None")
    if column.indexed:
        kwargs.append("index=True")
    if column.unique:
        kwargs.append("unique=True")
    if column.foreign_key:
        kwargs.append(f'foreign_key="{column.foreign_key}"')
    if not args and not kwargs and not column.nullable:
        return "Field()"
    return f"Field({', '.join([*args, *kwargs])})"


def _render_schemas(
    resources: list[ResourceSpec],
    *,
    auth_resource: ResourceSpec | None = None,
) -> str:
    imports = {"from sqlmodel import SQLModel"}
    if any(column.py_type == "date" for resource in resources for column in resource.columns):
        imports.add("from datetime import date")
    if any(column.py_type == "datetime" for resource in resources for column in resource.columns):
        imports.add("from datetime import datetime")
    blocks = ["\n".join(sorted(imports)), ""]
    for resource in resources:
        data_columns = [column for column in resource.columns if not column.primary_key]
        if resource is auth_resource:
            public_columns = [
                column
                for column in data_columns
                if column.name not in {"password", "password_hash", "hashed_password"}
            ]
            blocks.append(_schema_class(f"{resource.class_name}Base", public_columns, optional=False))
            blocks.append(
                f"\nclass {resource.class_name}Create({resource.class_name}Base):\n"
                "    password: str"
            )
            blocks.append(
                _schema_class(
                    f"{resource.class_name}Update",
                    [*public_columns, ColumnSpec(name="password", py_type="str", nullable=True)],
                    optional=True,
                )
            )
            read_columns = [
                ColumnSpec(name="id", py_type="int", nullable=False),
                *[column for column in public_columns if column.name != "id"],
            ]
            blocks.append(_schema_class(f"{resource.class_name}Read", read_columns, optional=False))
            blocks.append("")
            continue
        blocks.append(_schema_class(f"{resource.class_name}Base", data_columns, optional=False))
        blocks.append(f'\nclass {resource.class_name}Create({resource.class_name}Base):\n    __doc__ = "Create payload."')
        blocks.append(_schema_class(f"{resource.class_name}Update", data_columns, optional=True))
        read_columns = [
            ColumnSpec(name="id", py_type="int", nullable=False),
            *[column for column in data_columns if column.name != "id"],
        ]
        blocks.append(_schema_class(f"{resource.class_name}Read", read_columns, optional=False))
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def _schema_class(name: str, columns: list[ColumnSpec], *, optional: bool) -> str:
    lines = [f"\nclass {name}(SQLModel):"]
    if not columns:
        lines.append("    pass")
    for column in columns:
        annotation = f"{column.py_type} | None" if optional or column.nullable else column.py_type
        default = " = None" if optional or column.nullable else ""
        lines.append(f"    {column.name}: {annotation}{default}")
    return "\n".join(lines)


def _render_resource_router(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    capabilities: dict[str, bool],
    custom_routes: list[dict[str, Any]],
    *,
    auth_resource: ResourceSpec | None = None,
    access_rules: list[dict[str, Any]] | None = None,
    api_prefix: str = "",
) -> str:
    access_rules = access_rules or []
    dependency_import = ", Depends, HTTPException, Query, status"
    is_auth_resource = resource is auth_resource
    auth_symbols = ["get_current_user"]
    if is_auth_resource:
        auth_symbols.append("hash_password")
    if any(_safe_identifier(str(rule.get("behavior") or "")) == "role_restricted" for rule in access_rules):
        auth_symbols.append("require_role")
    auth_import = f"\nfrom auth import {', '.join(auth_symbols)}" if capabilities["auth"] else ""
    websocket_import = "\nfrom websockets import broadcast_notification" if capabilities["websocket"] else ""
    auth_param = ", current_user: dict = Depends(get_current_user)" if capabilities["auth"] else ""
    create_auth_param = "" if is_auth_resource else auth_param
    credential_create = ""
    credential_update = ""
    if is_auth_resource:
        password_field = _password_hash_field(resource)
        credential_create = (
            '    password = payload_data.pop("password")\n'
            f'    payload_data["{password_field}"] = hash_password(password)\n'
        )
        credential_update = (
            '    password = data.pop("password", None)\n'
            "    if password is not None:\n"
            f'        data["{password_field}"] = hash_password(password)\n'
        )
    async_prefix = "async " if capabilities["websocket"] else ""
    sqlalchemy_import = ""
    download_import = ""
    extra_model_imports = _extra_model_imports(resource, resources, custom_routes)
    extra_schema_imports = _extra_schema_imports(resource, resources, custom_routes)
    if any(route.get("kind") in {"top_related", "aggregate_count_by_field"} for route in _routes_for_resource(resource, custom_routes)):
        sqlalchemy_import = "\nfrom sqlalchemy import func"
    if any(route.get("kind") == "file_download" for route in _routes_for_resource(resource, custom_routes)):
        download_import = "\nimport csv\nfrom io import StringIO\n\nfrom fastapi.responses import Response"
    route_prefix = f"/{resource.route_name}"
    item_param = f"{_singular_snake(resource.table_name)}_id"
    item_path = f"{route_prefix}/{{{item_param}}}"
    create_role_rule = _matching_access_rule(
        access_rules, resource, "POST", route_prefix, "role_restricted"
    )
    create_guard = _role_guard(create_role_rule)
    list_guard = _role_guard(
        _matching_access_rule(access_rules, resource, "GET", route_prefix, "role_restricted")
    )
    detail_guard = _role_guard(
        _matching_access_rule(access_rules, resource, "GET", item_path, "role_restricted")
    )
    update_guard = _role_guard(
        _matching_access_rule(access_rules, resource, "PUT", item_path, "role_restricted")
    )
    delete_guard = _role_guard(
        _matching_access_rule(access_rules, resource, "DELETE", item_path, "role_restricted")
    )
    scope_rule = _matching_access_rule(
        access_rules,
        resource,
        "GET",
        route_prefix,
        "current_user_scoped",
    )
    scope_block, scope_resources = _scope_filter(resource, resources, scope_rule)
    for scope_resource in scope_resources:
        if scope_resource.class_name not in extra_model_imports:
            extra_model_imports.append(scope_resource.class_name)
    create_scope_rule = _matching_access_rule(
        access_rules,
        resource,
        "POST",
        route_prefix,
        "current_user_scoped",
    )
    create_scope_block, create_scope_resources = _create_scope_guard(
        resource,
        resources,
        create_scope_rule,
        create_role_rule,
    )
    for create_scope_resource in create_scope_resources:
        if create_scope_resource.class_name not in extra_model_imports:
            extra_model_imports.append(create_scope_resource.class_name)
    filter_params = "".join(
        f", {field}: {_field_type(resource, field)} | None = Query(default=None)" for field in resource.filter_fields
    )
    temporal_filter_types = {
        _field_type(resource, field)
        for field in resource.filter_fields
        if _field_type(resource, field) in {"date", "datetime"}
    }
    temporal_import = (
        f"from datetime import {', '.join(sorted(temporal_filter_types))}\n\n"
        if temporal_filter_types
        else ""
    )
    filter_lines = [
        f"    if {field} is not None:\n        statement = statement.where({resource.class_name}.{field} == {field})"
        for field in resource.filter_fields
    ]
    filter_block = "\n".join(filter_lines)
    if filter_block:
        filter_block += "\n"
    custom_block = _render_custom_routes(resource, resources, capabilities, custom_routes)
    mutation_payload = 'record.model_dump() if hasattr(record, "model_dump") else record.dict()'
    create_block = ""
    if "create" in resource.actions:
        notify = (
            f'    await broadcast_notification("{_singular_snake(resource.table_name)}.created", {mutation_payload})\n'
            if capabilities["websocket"]
            else ""
        )
        create_block = f"""
@router.post("", response_model={resource.class_name}Read, status_code=status.HTTP_201_CREATED)
{async_prefix}def create_{_singular_snake(resource.table_name)}(payload: {resource.class_name}Create, session: Session = Depends(get_session){create_auth_param}):
{create_guard}{create_scope_block}    payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
{credential_create}    record = {resource.class_name}(**payload_data)
    session.add(record)
    session.commit()
    session.refresh(record)
{notify}    return record

"""
    list_block = ""
    if "list" in resource.actions:
        list_block = f"""
@router.get("", response_model=list[{resource.class_name}Read])
def list_{resource.route_name}(session: Session = Depends(get_session){auth_param}{filter_params}):
{list_guard}    statement = select({resource.class_name})
{scope_block}{filter_block}    return session.exec(statement).all()

"""
    detail_block = ""
    if "detail" in resource.actions:
        detail_block = f"""
@router.get("/{{{item_param}}}", response_model={resource.class_name}Read)
def get_{_singular_snake(resource.table_name)}({item_param}: int, session: Session = Depends(get_session){auth_param}):
{detail_guard}    record = session.get({resource.class_name}, {item_param})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="{resource.class_name} not found")
    return record

"""
    update_block = ""
    if "update" in resource.actions:
        notify = (
            f'    await broadcast_notification("{_singular_snake(resource.table_name)}.updated", {mutation_payload})\n'
            if capabilities["websocket"]
            else ""
        )
        update_block = f"""
@router.put("/{{{item_param}}}", response_model={resource.class_name}Read)
{async_prefix}def update_{_singular_snake(resource.table_name)}({item_param}: int, payload: {resource.class_name}Update, session: Session = Depends(get_session){auth_param}):
{update_guard}    record = session.get({resource.class_name}, {item_param})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="{resource.class_name} not found")
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
{credential_update}    for field, value in data.items():
        setattr(record, field, value)
    session.add(record)
    session.commit()
    session.refresh(record)
{notify}    return record

"""
    delete_block = ""
    if "delete" in resource.actions:
        notify = (
            f'    await broadcast_notification("{_singular_snake(resource.table_name)}.deleted", {{"id": {item_param}}})\n'
            if capabilities["websocket"]
            else ""
        )
        delete_block = f"""
@router.delete("/{{{item_param}}}", status_code=status.HTTP_204_NO_CONTENT)
{async_prefix}def delete_{_singular_snake(resource.table_name)}({item_param}: int, session: Session = Depends(get_session){auth_param}):
{delete_guard}    record = session.get({resource.class_name}, {item_param})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="{resource.class_name} not found")
    session.delete(record)
    session.commit()
{notify}    return None

"""
    return f"""{temporal_import}from fastapi import APIRouter{dependency_import}
{sqlalchemy_import}{download_import}
from sqlmodel import Session, select

from database import get_session
from models import {", ".join([resource.class_name, *extra_model_imports])}
from schemas import {", ".join([f"{resource.class_name}Create", f"{resource.class_name}Read", f"{resource.class_name}Update", *extra_schema_imports])}{auth_import}{websocket_import}


router = APIRouter(prefix="{api_prefix}{route_prefix}", tags=["{resource.route_name}"])

{create_block}{list_block}{detail_block}{update_block}{delete_block}{custom_block}
"""


def _render_main(
    resources: list[ResourceSpec],
    capabilities: dict[str, bool],
    *,
    auth_resource: ResourceSpec | None = None,
) -> str:
    imports = [
        "from fastapi import FastAPI",
        "from database import create_db_and_tables",
    ]
    for resource in resources:
        imports.append(f"from routers.{resource.route_name} import router as {resource.router_var}")
    if auth_resource is not None:
        imports.append("from auth import router as auth_router")
    if capabilities["files"]:
        imports.append("from files import router as files_router")
    if capabilities["websocket"]:
        imports.append("from websockets import router as websocket_router")
    lines = [*imports, "", "", "app = FastAPI()", "", ""]
    lines.extend(
        [
            "@app.on_event(\"startup\")",
            "def on_startup() -> None:",
            "    create_db_and_tables()",
            "",
        ]
    )
    for resource in resources:
        lines.append(f"app.include_router({resource.router_var})")
    if auth_resource is not None:
        lines.append("app.include_router(auth_router)")
    if capabilities["files"]:
        lines.append("app.include_router(files_router)")
    if capabilities["websocket"]:
        lines.append("app.include_router(websocket_router)")
    lines.append("")
    return "\n".join(lines)


def _custom_route_suffix(route: dict[str, Any], resource: ResourceSpec, fallback: str) -> str:
    path = _canonical_path(route.get("path"))
    prefix = f"/{resource.route_name}"
    # Declared paths may carry the project-wide API prefix; router decorators
    # are always relative to the router's own prefix.
    marker = path.find(prefix + "/")
    if marker >= 0:
        return path[marker + len(prefix):]
    return fallback

def _routes_for_resource(resource: ResourceSpec, custom_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers = {
        resource.table_name.lower(),
        resource.route_name.lower(),
        _singular_snake(resource.table_name).lower(),
        resource.class_name.lower(),
    }
    selected: list[dict[str, Any]] = []
    for route in custom_routes:
        route_table = str(route.get("parent_table") or route.get("table") or "").lower()
        if route_table in markers:
            selected.append(route)
    return selected


def _extra_model_imports(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    custom_routes: list[dict[str, Any]],
) -> list[str]:
    imports: list[str] = []
    by_table = _resources_by_table(resources)
    for route in _routes_for_resource(resource, custom_routes):
        for key in ("child_table", "table", "data_table"):
            table = str(route.get(key) or "")
            related = by_table.get(_plural_snake(table))
            if related and related.class_name != resource.class_name:
                imports.append(related.class_name)
        if route.get("kind") == "file_download" and route.get("scoped"):
            data_resource = by_table.get(_plural_snake(str(route.get("data_table") or "")))
            if data_resource is not None:
                _scope_lines, scope_resources = _scope_filter(
                    data_resource,
                    resources,
                    {"behavior": "current_user_scoped"},
                )
                imports.extend(
                    item.class_name
                    for item in scope_resources
                    if item.class_name != resource.class_name
                )
    return list(dict.fromkeys(imports))


def _extra_schema_imports(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    custom_routes: list[dict[str, Any]],
) -> list[str]:
    imports: list[str] = []
    by_table = _resources_by_table(resources)
    for route in _routes_for_resource(resource, custom_routes):
        if route.get("kind") == "parent_children_paginated":
            related = by_table.get(_plural_snake(str(route.get("child_table") or "")))
            if related:
                imports.append(f"{related.class_name}Read")
    return list(dict.fromkeys(imports))


def _render_custom_routes(
    resource: ResourceSpec,
    resources: list[ResourceSpec],
    capabilities: dict[str, bool],
    custom_routes: list[dict[str, Any]],
) -> str:
    blocks: list[str] = []
    by_table = _resources_by_table(resources)
    auth_param = ", current_user: dict = Depends(get_current_user)" if capabilities["auth"] else ""
    for route in _routes_for_resource(resource, custom_routes):
        kind = str(route.get("kind") or "")
        if kind == "top_related":
            child = by_table.get(_plural_snake(str(route.get("child_table") or "")))
            if child is None:
                continue
            parent_id = f"{_singular_snake(resource.table_name)}_id"
            child_fk = _child_fk_for_parent(child, resource) or parent_id
            default_limit = int(route.get("limit") or 10)
            function_name = f"top_{resource.route_name}_by_{child.route_name}"
            blocks.append(
                f"""

@router.get("{_custom_route_suffix(route, resource, f'/top-by-{child.route_name}')}")
def {function_name}(limit: int = Query(default={default_limit}, ge=1, le=100), session: Session = Depends(get_session){auth_param}):
    statement = (
        select({resource.class_name}, func.count({child.class_name}.id).label("{child.route_name}_count"))
        .join({child.class_name}, {child.class_name}.{child_fk} == {resource.class_name}.id)
        .group_by({resource.class_name}.id)
        .order_by(func.count({child.class_name}.id).desc())
        .limit(limit)
    )
    return [
        {{"{_singular_snake(resource.table_name)}": item[0], "{child.route_name}_count": item[1]}}
        for item in session.exec(statement).all()
    ]
"""
            )
        elif kind == "parent_children_paginated":
            child = by_table.get(_plural_snake(str(route.get("child_table") or "")))
            if child is None:
                continue
            parent_id = f"{_singular_snake(resource.table_name)}_id"
            child_fk = _child_fk_for_parent(child, resource) or parent_id
            default_size = int(route.get("page_size") or 10)
            function_name = f"list_{child.route_name}_for_{_singular_snake(resource.table_name)}"
            blocks.append(
                f"""

@router.get("/{{{parent_id}}}/{child.route_name}", response_model=list[{child.class_name}Read])
def {function_name}({parent_id}: int, page: int = Query(default=1, ge=1), page_size: int = Query(default={default_size}, ge=1, le=100), session: Session = Depends(get_session){auth_param}):
    offset = (page - 1) * page_size
    statement = (
        select({child.class_name})
        .where({child.class_name}.{child_fk} == {parent_id})
        .offset(offset)
        .limit(page_size)
    )
    return session.exec(statement).all()
"""
            )
        elif kind == "aggregate_count_by_field":
            field = _safe_identifier(str(route.get("field") or ""))
            if not field:
                continue
            function_name = f"{_singular_snake(resource.table_name)}_{field}_summary"
            blocks.append(
                f"""

@router.get("/{field}-summary")
def {function_name}(session: Session = Depends(get_session){auth_param}):
    statement = select({resource.class_name}.{field}, func.count({resource.class_name}.id)).group_by({resource.class_name}.{field})
    return [{{"{field}": item[0], "count": item[1]}} for item in session.exec(statement).all()]
"""
            )
        elif kind == "file_download":
            data_resource = by_table.get(_plural_snake(str(route.get("data_table") or resource.table_name)))
            if data_resource is None:
                continue
            available_fields = {column.name for column in data_resource.columns}
            fields = [
                field
                for field in route.get("fields") or []
                if field in available_fields and field != "id"
            ]
            if not fields:
                fields = [
                    field
                    for field in ("title", "body", "description", "course_name", "due_date")
                    if field in available_fields
                ]
            if not fields:
                fields = [
                    column.name
                    for column in data_resource.columns
                    if column.name != "id" and "password" not in column.name
                ][:4]
            statement_lines = [f"    statement = select({data_resource.class_name})"]
            path = _canonical_path(route.get("path")).lower()
            if "pinned" in path and "pinned" in available_fields:
                statement_lines.append(
                    f"    statement = statement.where({data_resource.class_name}.pinned == True)"
                )
            if "unfinished" in path and "status" in available_fields:
                statement_lines.append(
                    f'    statement = statement.where({data_resource.class_name}.status.notin_(["completed", "finished"]))'
                )
            if route.get("scoped"):
                scope_lines, _ = _scope_filter(
                    data_resource,
                    resources,
                    {"behavior": "current_user_scoped"},
                )
                statement_lines.extend(line for line in scope_lines.rstrip().splitlines() if line)
            statement_block = "\n".join(statement_lines)
            suffix = _custom_route_suffix(route, resource, "/download")
            function_name = "download_" + "_".join(
                part
                for part in (_safe_identifier(item) for item in path.strip("/").split("/"))
                if part and not part.startswith("{")
            )
            response_format = str(route.get("format") or "text").lower()
            if response_format == "csv":
                csv_headers = repr(fields)
                csv_values = ", ".join(
                    f'getattr(record, "{field}", "")'
                    for field in fields
                )
                body = f"""    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow({csv_headers})
    for record in records:
        writer.writerow([{csv_values}])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={{"Content-Disposition": 'attachment; filename="report.csv"'}},
    )"""
            else:
                text_values = "\n".join(
                    f'            "{field.replace("_", " ").title()}: " + str(getattr(record, "{field}", "")),'
                    for field in fields
                )
                body = f"""    sections: list[str] = []
    for record in records:
        sections.append("\\n".join([
{text_values}
        ]))
    return Response(
        content="\\n\\n".join(sections),
        media_type="text/plain",
        headers={{"Content-Disposition": 'attachment; filename="report.txt"'}},
    )"""
            blocks.append(
                f"""

@router.get("{suffix}")
def {function_name}(session: Session = Depends(get_session){auth_param}):
{statement_block}
    records = session.exec(statement).all()
{body}
"""
            )
    return "".join(blocks)


def _resources_by_table(resources: list[ResourceSpec]) -> dict[str, ResourceSpec]:
    result: dict[str, ResourceSpec] = {}
    for resource in resources:
        for marker in {resource.table_name, resource.route_name, _singular_snake(resource.table_name), resource.class_name}:
            result[_plural_snake(marker)] = resource
    return result


def _child_fk_for_parent(child: ResourceSpec, parent: ResourceSpec) -> str | None:
    candidates = {
        f"{_singular_snake(parent.table_name)}_id",
        f"{_singular_snake(parent.route_name)}_id",
    }
    for column in child.columns:
        if column.name in candidates:
            return column.name
    return None


def _render_auth(auth_resource: ResourceSpec, *, api_prefix: str = "") -> str:
    password_field = _password_hash_field(auth_resource)
    return f"""import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import {auth_resource.class_name}


SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
router = APIRouter(prefix="{api_prefix}/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def hash_password(password: str) -> str:
    iterations = 390_000
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${{iterations}}${{salt.hex()}}${{digest.hex()}}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def require_role(current_user: {auth_resource.class_name}, *roles: str) -> None:
    if getattr(current_user, "role", None) not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )


def create_access_token(subject: str, expires_minutes: int = 60) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload: dict[str, Any] = {{"sub": subject, "exp": expires_at}}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> {auth_resource.class_name}:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={{"WWW-Authenticate": "Bearer"}},
        ) from exc
    subject = payload.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials") from exc
    user = session.get({auth_resource.class_name}, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")
    return user


@router.post("/token")
def login_for_access_token(
    credentials: LoginRequest,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    statement = select({auth_resource.class_name}).where(
        {auth_resource.class_name}.username == credentials.username
    )
    user = session.exec(statement).first()
    if user is None or not verify_password(credentials.password, user.{password_field}):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={{"WWW-Authenticate": "Bearer"}},
        )
    return {{
        "access_token": create_access_token(str(user.id)),
        "token_type": "bearer",
    }}
"""


def _render_files_router(auth_enabled: bool, *, api_prefix: str = "") -> str:
    auth_import = "\nfrom auth import get_current_user" if auth_enabled else ""
    auth_param = ", current_user: dict = Depends(get_current_user)" if auth_enabled else ""
    return f"""from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse{auth_import}


UPLOAD_DIR = Path("uploads")
router = APIRouter(prefix="{api_prefix}/files", tags=["files"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def upload_file(file: UploadFile = File(...){auth_param}):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "uploaded_file").name
    target = UPLOAD_DIR / safe_name
    target.write_bytes(await file.read())
    return {{"filename": safe_name}}


@router.get("/{{filename}}")
def download_file(filename: str{auth_param}):
    target = UPLOAD_DIR / Path(filename).name
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(target)
"""


def _render_websocket_router() -> str:
    return """from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter(tags=["websockets"])
active_connections: list[WebSocket] = []


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_json({"message": message})
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        return None


async def broadcast_notification(event: str, payload: dict[str, Any]) -> None:
    for connection in list(active_connections):
        await connection.send_json({"event": event, "payload": payload})
"""


def _render_requirements(capabilities: dict[str, bool]) -> str:
    packages = ["fastapi", "uvicorn", "sqlmodel"]
    if capabilities["auth"]:
        packages.append("python-jose[cryptography]")
    if capabilities["files"]:
        packages.append("python-multipart")
    return "\n".join(packages) + "\n"


def _field_type(resource: ResourceSpec, field: str) -> str:
    for column in resource.columns:
        if column.name == field:
            return column.py_type
    return "str"


def _python_type(raw_type: Any) -> str:
    text = str(raw_type or "").lower()
    if any(term in text for term in ("bool",)):
        return "bool"
    if any(term in text for term in ("int", "serial", "bigint", "smallint")):
        return "int"
    if any(term in text for term in ("float", "double", "decimal", "numeric", "real")):
        return "float"
    if "datetime" in text or "timestamp" in text:
        return "datetime"
    if re.search(r"\bdate\b", text):
        return "date"
    return "str"


def _class_name(value: str) -> str:
    singular = _singular_snake(value)
    return "".join(part.capitalize() for part in singular.split("_") if part) or "Record"


def _singular_snake(value: str) -> str:
    text = _safe_identifier(value)
    if text.endswith("ies") and len(text) > 3:
        return f"{text[:-3]}y"
    if text.endswith("s") and not text.endswith("ss") and len(text) > 1:
        return text[:-1]
    return text


def _plural_snake(value: str) -> str:
    text = _safe_identifier(value)
    if not text:
        return "records"
    if text.endswith("s"):
        return text
    if text.endswith("y") and len(text) > 1:
        return f"{text[:-1]}ies"
    return f"{text}s"


def _safe_identifier(value: str) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = f"field_{text}"
    return text


def _canonical_path(path: Any) -> str:
    value = str(path or "").strip().rstrip(".,;")
    if not value:
        return ""
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") if len(value) > 1 else value


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value)
    if text.lower() in {"true", "false"}:
        return text.capitalize()
    return repr(text)
