"""Static project indexer for generated FastAPI projects.

The chat assistant should use compact project metadata first and only ask for
full file content when a precise edit requires it. This service keeps that
metadata in `project_helpers`.
"""
from __future__ import annotations

import ast
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.project import Project
from app.repositories.chat import ProjectHelperRepository

MAX_FILE_BYTES = 256_000
API_DOC_PATH = "API_ENDPOINTS.md"
PATH_PARAM_RE = re.compile(r"{([^}:]+)")


def _name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Constant):
        return repr(node.value)
    try:
        return ast.unparse(node)
    except Exception:
        return node.__class__.__name__


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return _name(node)


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _markdown_inline(value: Any) -> str:
    return str(value).replace("\n", " ").strip()


def _call_keyword(call: ast.Call, key: str) -> Any:
    for kw in call.keywords:
        if kw.arg == key:
            return _literal(kw.value)
    return None


def _is_base(cls: ast.ClassDef, base_name: str) -> bool:
    return any(_name(base).split(".")[-1] == base_name for base in cls.bases)


def _is_sqlmodel_table(cls: ast.ClassDef) -> bool:
    if not _is_base(cls, "SQLModel"):
        return False
    return any(kw.arg == "table" and _literal(kw.value) is True for kw in cls.keywords)


def _class_tablename(cls: ast.ClassDef) -> str:
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "__tablename__":
                value = _literal(stmt.value)
                return value if isinstance(value, str) else ""
    return ""


def _is_pydantic_model(cls: ast.ClassDef) -> bool:
    return any(_name(base).split(".")[-1] in {"BaseModel", "Schema"} for base in cls.bases)


def _is_sqlalchemy_model(cls: ast.ClassDef) -> bool:
    return bool(_class_tablename(cls)) and not _is_pydantic_model(cls)


def _field_info(value: ast.AST | None) -> dict[str, Any]:
    if not isinstance(value, ast.Call) or _name(value.func).split(".")[-1] != "Field":
        return {}
    keys = ("primary_key", "foreign_key", "index", "max_length", "default")
    return {key: _call_keyword(value, key) for key in keys if _call_keyword(value, key) is not None}


def _column_field_info(value: ast.AST | None) -> dict[str, Any]:
    if not isinstance(value, ast.Call):
        return {}
    call_name = _name(value.func).split(".")[-1]
    if call_name not in {"Column", "mapped_column"}:
        return {}
    info: dict[str, Any] = {}
    if value.args:
        info["type"] = _name(value.args[0])
    for key in ("primary_key", "index", "nullable", "default", "unique"):
        raw = _call_keyword(value, key)
        if raw is not None:
            info[key] = raw
    for arg in value.args:
        if isinstance(arg, ast.Call) and _name(arg.func).split(".")[-1] == "ForeignKey" and arg.args:
            fk = _literal(arg.args[0])
            if isinstance(fk, str):
                info["foreign_key"] = fk
    fk_kw = _call_keyword(value, "foreign_key")
    if isinstance(fk_kw, str):
        info["foreign_key"] = fk_kw
    return info


def _route_object_prefixes(tree: ast.Module) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call_name = _name(node.value.func).split(".")[-1]
        if call_name not in {"APIRouter", "FastAPI"}:
            continue
        prefix = _call_keyword(node.value, "prefix") if call_name == "APIRouter" else ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix if isinstance(prefix, str) else ""
    return prefixes


def _router_prefix(tree: ast.Module) -> str:
    for name, prefix in _route_object_prefixes(tree).items():
        if name != "app":
            return prefix
    return ""


def _route_decorator(dec: ast.AST, route_prefixes: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    route_object = _name(func.value)
    if route_object not in route_prefixes:
        return None
    method = func.attr.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "WEBSOCKET"}:
        return None
    path = _literal(dec.args[0]) if dec.args else ""
    return {
        "method": method,
        "prefix": route_prefixes.get(route_object) or "",
        "path": path if isinstance(path, str) else "",
        "response_model": _name(next((kw.value for kw in dec.keywords if kw.arg == "response_model"), None)),
        "status_code": _call_keyword(dec, "status_code"),
    }


def _call_tail(node: ast.AST | None) -> str:
    return _name(node).split(".")[-1]


def _default_parameter_kind(default: ast.AST | None) -> str:
    if isinstance(default, ast.Call):
        tail = _call_tail(default.func).lower()
        if tail in {"depends", "file", "form", "query", "path", "body", "header", "cookie"}:
            return tail
    if default is None:
        return "required"
    return "default"


def _parameter_default(default: ast.AST | None) -> Any:
    if default is None:
        return None
    if isinstance(default, ast.Call) and default.args:
        return _literal(default.args[0])
    return _literal(default)


def _looks_like_body_schema(annotation: str) -> bool:
    tail = annotation.split(".")[-1]
    return tail.endswith(("Create", "Update", "Request", "Input", "Payload", "Schema"))


def _function_parameters(fn: ast.FunctionDef | ast.AsyncFunctionDef, route_path: str) -> list[dict[str, Any]]:
    path_names = set(PATH_PARAM_RE.findall(route_path))
    args = list(fn.args.args)
    defaults: list[ast.AST | None] = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    params: list[dict[str, Any]] = []

    for arg, default in zip(args, defaults, strict=False):
        if arg.arg in {"self"}:
            continue
        annotation = _name(arg.annotation) or "Any"
        default_kind = _default_parameter_kind(default)
        default_value = _parameter_default(default)

        if default_kind == "depends":
            location = "dependency"
            required = False
        elif arg.arg in path_names or default_kind == "path":
            location = "path"
            required = True
        elif default_kind in {"file", "form", "header", "cookie"}:
            location = default_kind
            required = default_value is None or default_value is Ellipsis or default_value == "..."
        elif default_kind == "body" or (default is None and _looks_like_body_schema(annotation)):
            location = "body"
            required = True
        elif arg.arg == "request":
            location = "request"
            required = False
        else:
            location = "query"
            required = default is None

        item: dict[str, Any] = {
            "name": arg.arg,
            "in": location,
            "type": annotation,
            "required": required,
        }
        if default_value is not None and default_value is not Ellipsis:
            item["default"] = default_value
        params.append(item)

    return params


def _body_schema_names(params: list[dict[str, Any]]) -> list[str]:
    return [
        str(param.get("type") or "")
        for param in params
        if param.get("in") == "body" and param.get("type")
    ]


def _compare_operator(op: ast.cmpop) -> str:
    return {
        ast.Eq: "==",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "is",
        ast.IsNot: "is not",
        ast.In: "in",
        ast.NotIn: "not in",
    }.get(type(op), op.__class__.__name__)


def _query_filters(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, rel_path: str) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Attribute):
            continue
        table = _name(node.left.value)
        field = node.left.attr
        if not table or table in {"self", "payload", "obj"}:
            continue
        filters.append(
            {
                "file": rel_path,
                "function": fn.name,
                "line": node.lineno,
                "table": table.split(".")[-1],
                "field": field,
                "operator": _compare_operator(node.ops[0]) if node.ops else "",
                "value": _name(node.comparators[0]) if node.comparators else "",
            }
        )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in filters:
        marker = (
            item["function"],
            item["table"].lower(),
            item["field"].lower(),
            item["operator"],
        )
        if marker not in seen:
            deduped.append(item)
            seen.add(marker)
    return deduped


def _body_symbols(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    symbols: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
            full = _name(node)
            if full:
                symbols.add(full)
        elif isinstance(node, ast.Call):
            call_name = _name(node.func)
            if call_name:
                symbols.add(call_name)
                symbols.add(call_name.split(".")[-1])
    return sorted(symbols)[:120]


def _class_fields(cls: ast.ClassDef, rel_path: str) -> dict[str, Any]:
    fields = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            field = {
                "name": stmt.target.id,
                "type": _name(stmt.annotation),
                "line": stmt.lineno,
            }
            field.update(_field_info(stmt.value))
            column_info = _column_field_info(stmt.value)
            if column_info:
                field.update({key: value for key, value in column_info.items() if key != "type"})
                if field.get("type") in {"", "Any"} or str(field.get("type", "")).startswith("Mapped"):
                    field["type"] = column_info.get("type") or field.get("type")
            fields.append(field)
        elif isinstance(stmt, ast.Assign):
            column_info = _column_field_info(stmt.value)
            if not column_info:
                continue
            for target in stmt.targets:
                if not isinstance(target, ast.Name):
                    continue
                field = {
                    "name": target.id,
                    "type": column_info.get("type") or "",
                    "line": stmt.lineno,
                }
                field.update({key: value for key, value in column_info.items() if key != "type"})
                fields.append(field)
    return {
        "file": rel_path,
        "name": cls.name,
        "db_table_name": _class_tablename(cls),
        "line": cls.lineno,
        "bases": [_name(base) for base in cls.bases],
        "fields": fields,
        "is_sqlmodel_table": _is_sqlmodel_table(cls),
        "is_sqlalchemy_table": _is_sqlalchemy_model(cls),
        "is_pydantic_model": _is_pydantic_model(cls),
    }


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in fn.args.args]
    if fn.args.vararg:
        args.append(f"*{fn.args.vararg.arg}")
    args.extend(arg.arg for arg in fn.args.kwonlyargs)
    if fn.args.kwarg:
        args.append(f"**{fn.args.kwarg.arg}")
    return f"{fn.name}({', '.join(args)})"


def _summarize_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    rel_path: str,
    routes: list[dict[str, Any]],
) -> dict[str, Any]:
    route = next((r for r in routes if r["function"] == fn.name and r["file"] == rel_path), None)
    summary = (
        f"Handles {route['method']} {route['path']}"
        if route
        else (ast.get_docstring(fn) or f"Function `{fn.name}`.")
    )
    return {
        "file": rel_path,
        "name": fn.name,
        "signature": _signature(fn),
        "line": fn.lineno,
        "is_async": isinstance(fn, ast.AsyncFunctionDef),
        "route": route,
        "body_symbols": _body_symbols(fn),
        "query_filters": _query_filters(fn, rel_path=rel_path),
        "summary": summary[:300],
    }


def _module_exists(project_root: Path, module: str) -> bool:
    rel = Path(*module.split("."))
    return (
        (project_root / f"{rel.as_posix()}.py").is_file()
        or (project_root / rel / "__init__.py").is_file()
    )


def _local_import_errors(project_root: Path, tree: ast.AST) -> list[str]:
    local_roots = {
        path.stem
        for path in project_root.glob("*.py")
        if path.name != "__init__.py"
    }
    local_roots.update(
        path.name
        for path in project_root.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )
    generated_roots = local_roots | {"auth", "database", "connections", "routers"}
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or not node.module:
            continue
        module = node.module
        root_name = module.split(".", 1)[0]
        if root_name not in generated_roots:
            continue
        if not _module_exists(project_root, module):
            errors.append(f"unresolved local import `{module}`")
    return errors


def _imported_symbols(tree: ast.Module) -> tuple[list[str], dict[str, dict[str, str]]]:
    imports: list[str] = []
    aliases: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                aliases[alias.asname or alias.name.split(".", 1)[0]] = {
                    "module": alias.name,
                    "symbol": "",
                    "imported": alias.name,
                }
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported = f"{module}.{alias.name}" if module else alias.name
                imports.append(imported)
                aliases[alias.asname or alias.name] = {
                    "module": module,
                    "symbol": alias.name,
                    "imported": imported,
                }
    return sorted(set(imports)), aliases


def _router_file_from_module(module: str) -> str:
    if not module:
        return ""
    parts = module.split(".")
    return f"{'/'.join(parts)}.py"


def _router_file_from_include_arg(name: str, aliases: dict[str, dict[str, str]]) -> str:
    imported = aliases.get(name)
    if not imported and "." in name:
        imported = aliases.get(name.split(".", 1)[0])
    if not imported:
        if name.endswith(".router"):
            return _router_file_from_module(name.rsplit(".", 1)[0])
        return ""

    symbol = imported.get("symbol", "")
    module = imported.get("module", "")
    imported_name = imported.get("imported", "")
    if symbol and not symbol.endswith("router"):
        return _router_file_from_module(imported_name)
    return _router_file_from_module(module or imported_name)


def _router_wiring(tree: ast.Module, *, rel_path: str, aliases: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    wiring: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "include_router":
            continue
        router_name = _name(node.args[0]) if node.args else ""
        imported = aliases.get(router_name, {})
        router_file = _router_file_from_include_arg(router_name, aliases)
        wiring.append(
            {
                "file": rel_path,
                "line": node.lineno,
                "router": router_name,
                "imported": imported,
                "router_file": router_file,
            }
        )
    return wiring


def _parse_python_file(path: Path, root: Path) -> dict[str, Any]:
    rel_path = path.relative_to(root).as_posix()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    file_info: dict[str, Any] = {
        "path": rel_path,
        "bytes": len(data),
        "sha256": digest,
        "language": "python",
    }
    if len(data) > MAX_FILE_BYTES:
        file_info["skipped"] = "too_large"
        return {
            "file": file_info,
            "tables": [],
            "routes": [],
            "functions": [],
            "classes": [],
            "query_filters": [],
            "router_wiring": [],
        }

    try:
        source = data.decode("utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception as exc:
        file_info["parse_error"] = str(exc)
        return {
            "file": file_info,
            "tables": [],
            "routes": [],
            "functions": [],
            "classes": [],
            "query_filters": [],
            "router_wiring": [],
        }

    imports, import_aliases = _imported_symbols(tree)
    route_prefixes = _route_object_prefixes(tree)
    tables: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_info = _class_fields(node, rel_path)
            classes.append(class_info)
            if class_info["is_sqlmodel_table"] or class_info["is_sqlalchemy_table"]:
                tables.append({
                    "file": rel_path,
                    "name": node.name,
                    "db_table_name": class_info.get("db_table_name") or "",
                    "line": node.lineno,
                    "fields": class_info["fields"],
                    "kind": "sqlmodel" if class_info["is_sqlmodel_table"] else "sqlalchemy",
                })

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                route = _route_decorator(dec, route_prefixes)
                if not route:
                    continue
                method = route["method"]
                path_part = route["path"]
                path_value = f"{route.get('prefix') or ''}{path_part}"
                params = _function_parameters(node, path_value or "/")
                routes.append({
                    "file": rel_path,
                    "method": method,
                    "path": path_value or "/",
                    "function": node.name,
                    "line": node.lineno,
                    "parameters": params,
                    "response_model": route.get("response_model") or "",
                    "body_schema_names": _body_schema_names(params),
                    "status_code": route.get("status_code"),
                })

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_summarize_function(node, rel_path=rel_path, routes=routes))

    query_filters = [
        item
        for fn in functions
        for item in (fn.get("query_filters") or [])
    ]
    router_wiring = _router_wiring(tree, rel_path=rel_path, aliases=import_aliases)
    local_import_errors = _local_import_errors(root, tree)
    if local_import_errors:
        file_info["local_import_errors"] = local_import_errors
    if tables:
        file_info["tables"] = [t["name"] for t in tables]
    if routes:
        file_info["routes"] = [f"{r['method']} {r['path']}" for r in routes]
    if classes:
        file_info["classes"] = [c["name"] for c in classes]
        file_info["class_fields"] = [
            f"{class_info['name']}.{field['name']}"
            for class_info in classes
            for field in class_info.get("fields") or []
        ][:80]
    if imports:
        file_info["imports"] = sorted(set(imports))[:24]
    if router_wiring:
        file_info["router_wiring"] = router_wiring
    return {
        "file": file_info,
        "tables": tables,
        "routes": routes,
        "functions": functions,
        "classes": classes,
        "query_filters": query_filters,
        "router_wiring": router_wiring,
    }


def build_project_index(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    query_filters: list[dict[str, Any]] = []
    router_wiring: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*.py")):
        if any(part in {"__pycache__", ".venv", ".venv_gen"} for part in path.parts):
            continue
        parsed = _parse_python_file(path, root)
        files.append(parsed["file"])
        tables.extend(parsed["tables"])
        routes.extend(parsed["routes"])
        functions.extend(parsed["functions"])
        classes.extend(parsed["classes"])
        query_filters.extend(parsed["query_filters"])
        router_wiring.extend(parsed["router_wiring"])

    root_hash = hashlib.sha256(
        "\n".join(
            f"{item.get('path')}:{item.get('sha256')}"
            for item in sorted(files, key=lambda f: str(f.get("path") or ""))
            if item.get("sha256")
        ).encode("utf-8")
    ).hexdigest()

    return {
        "database_schema": {"tables": tables},
        "api_routes": routes,
        "file_index": files,
        "function_summaries": functions,
        "class_summaries": classes,
        "query_filters": query_filters,
        "router_wiring": router_wiring,
        "stats": {
            "python_files": len(files),
            "tables": len(tables),
            "routes": len(routes),
            "functions": len(functions),
            "classes": len(classes),
            "query_filters": len(query_filters),
            "router_wiring": len(router_wiring),
        },
        "index_meta": {
            "folder_exists": root.exists(),
            "root_sha256": root_hash,
        },
    }


def _route_summary(route: dict[str, Any]) -> str:
    method = route.get("method", "")
    path = route.get("path", "")
    function = route.get("function", "")
    if path == "/auth/login":
        return "Authenticates a user and returns an access token."
    if path == "/auth/register":
        return "Creates a user account and returns an access token."
    if path == "/health":
        return "Health check endpoint for uptime/readiness checks."
    if method == "GET" and "{" not in path:
        return "Lists resources or returns a collection for this route."
    if method == "GET":
        return "Returns one resource or file by identifier."
    if method == "POST":
        return "Creates a resource, uploads data, or performs an action."
    if method in {"PUT", "PATCH"}:
        return "Updates an existing resource by identifier."
    if method == "DELETE":
        return "Deletes an existing resource by identifier."
    return f"Handled by `{function}`."


def _format_parameters(route: dict[str, Any]) -> str:
    params = route.get("parameters") or []
    if not params:
        return "-"
    rendered = []
    for param in params:
        location = param.get("in") or "query"
        name = param.get("name") or ""
        annotation = param.get("type") or "Any"
        required = "required" if param.get("required") else "optional"
        default = param.get("default")
        default_text = "" if default is None else f", default `{_markdown_cell(default)}`"
        rendered.append(
            f"`{_markdown_cell(location)}` `{_markdown_cell(name)}: {_markdown_cell(annotation)}` "
            f"({required}{default_text})"
        )
    return "<br>".join(rendered)


def _route_group(route: dict[str, Any]) -> str:
    path = str(route.get("path") or "/").strip("/")
    if path:
        return path.split("/", 1)[0].replace("-", " ").replace("_", " ").title()
    file = str(route.get("file") or "").rsplit("/", 1)[-1]
    return file.rsplit(".", 1)[0].replace("_", " ").title() if file else "Root"


def _format_parameter_bullets(params: list[dict[str, Any]]) -> list[str]:
    if not params:
        return ["  - None detected."]

    lines: list[str] = []
    for param in params:
        location = _markdown_inline(param.get("in") or "query")
        name = _markdown_inline(param.get("name") or "")
        annotation = _markdown_inline(param.get("type") or "Any")
        required = "required" if param.get("required") else "optional"
        default = param.get("default")
        default_text = "" if default is None else f", default `{_markdown_inline(default)}`"
        lines.append(f"  - `{name}`: `{annotation}` in `{location}` ({required}{default_text})")
    return lines


def _split_route_parameters(route: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    request_params: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    for param in route.get("parameters") or []:
        if param.get("in") == "dependency":
            dependencies.append(param)
        else:
            request_params.append(param)
    return request_params, dependencies


def _client_parameters(route: dict[str, Any]) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []
    for param in route.get("parameters") or []:
        location = param.get("in")
        annotation = str(param.get("type") or "")
        if location == "dependency":
            if annotation.endswith("OAuth2PasswordRequestForm"):
                params.extend([
                    {"name": "email", "in": "form", "type": "str", "required": True},
                    {"name": "password", "in": "form", "type": "str", "required": True},
                ])
            continue
        if location == "request":
            continue
        params.append(param)
    return params


def render_api_endpoint_document(index: dict[str, Any]) -> str:
    routes = sorted(
        index.get("api_routes") or [],
        key=lambda r: (_route_group(r), str(r.get("path", "")), str(r.get("method", ""))),
    )
    stats = index.get("stats") or {}
    method_counts: dict[str, int] = {}
    for route in routes:
        method = str(route.get("method") or "UNKNOWN")
        method_counts[method] = method_counts.get(method, 0) + 1

    lines = [
        "# API Endpoints",
        "",
        "This file is generated from the current FastAPI routers. It is updated after project generation and file uploads.",
        "",
        "## Overview",
        "",
        f"- Routes: {stats.get('routes', len(routes))}",
        f"- SQLModel tables: {stats.get('tables', 0)}",
        f"- Python files: {stats.get('python_files', 0)}",
        f"- Functions: {stats.get('functions', 0)}",
        "",
        "## Method Summary",
        "",
    ]
    if method_counts:
        for method, count in sorted(method_counts.items()):
            lines.append(f"- `{method}`: {count}")
    else:
        lines.append("- No API routes were detected.")
    lines.extend(["", "## Endpoints", ""])

    if not routes:
        lines.append("No API routes were detected.")
    else:
        current_group = ""
        for route in routes:
            group = _route_group(route)
            if group != current_group:
                if current_group:
                    lines.append("")
                current_group = group
                lines.append(f"### {group}")
                lines.append("")

            method = _markdown_inline(route.get("method", ""))
            path = _markdown_inline(route.get("path", ""))
            file = _markdown_inline(route.get("file", ""))
            line = route.get("line", "")
            handler = _markdown_inline(route.get("function", ""))
            location = f"{file}:{line}" if line else file
            request_params = _client_parameters(route)

            lines.append(f"#### `{method} {path}`")
            lines.append("")
            lines.append(f"- Purpose: {_route_summary(route)}")
            lines.append(f"- Handler: `{handler}`")
            lines.append(f"- Source: `{location}`")
            lines.append("- Parameters:")
            lines.extend(_format_parameter_bullets(request_params))
            lines.append("")

    tables = (index.get("database_schema") or {}).get("tables") or []
    lines.extend(["", "## Database Tables", ""])
    if not tables:
        lines.append("No SQLModel tables were detected.")
    for table in sorted(tables, key=lambda t: t.get("name", "")):
        lines.append(f"### {table.get('name', 'Unknown')}")
        lines.append("")
        lines.append("| Field | Type | Notes |")
        lines.append("| --- | --- | --- |")
        for field in table.get("fields", []):
            notes = []
            if field.get("primary_key"):
                notes.append("primary key")
            if field.get("foreign_key"):
                notes.append(f"FK {field['foreign_key']}")
            if field.get("index"):
                notes.append("indexed")
            if field.get("max_length"):
                notes.append(f"max {field['max_length']}")
            lines.append(
                f"| `{field.get('name', '')}` | `{field.get('type', '')}` | {', '.join(notes) or '-'} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_api_endpoint_document(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    target = root / API_DOC_PATH
    content = render_api_endpoint_document(index)
    old = target.read_text(encoding="utf-8") if target.exists() else None
    changed = old != content
    if changed:
        target.write_text(content, encoding="utf-8", newline="\n")
    data = content.encode("utf-8")
    return {
        "path": API_DOC_PATH,
        "bytes_written": len(data),
        "changed": changed,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class ProjectIndexer:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.helpers = ProjectHelperRepository(db)

    async def refresh(
        self,
        project: Project,
        *,
        reason: str = "index",
        write_endpoint_doc: bool = True,
    ) -> dict[str, Any]:
        root = Path(project.folder_path)
        if root.exists():
            index = build_project_index(root)
            target = root / API_DOC_PATH
            if write_endpoint_doc:
                endpoint_doc = write_api_endpoint_document(root, index)
            else:
                data = target.read_bytes() if target.is_file() else b""
                endpoint_doc = {
                    "path": API_DOC_PATH,
                    "bytes_written": len(data),
                    "changed": False,
                    "sha256": hashlib.sha256(data).hexdigest() if data else "",
                    "write_skipped": True,
                }
            if target.is_file() and not any(item.get("path") == API_DOC_PATH for item in index["file_index"]):
                index["file_index"].append({
                    "path": API_DOC_PATH,
                    "bytes": endpoint_doc["bytes_written"],
                    "sha256": endpoint_doc["sha256"],
                    "language": "markdown",
                    "generated": True,
                })
        else:
            endpoint_doc = {
                "path": API_DOC_PATH,
                "bytes_written": 0,
                "changed": False,
                "sha256": "",
                "missing_folder": True,
            }
            index = {
                "database_schema": {"tables": []},
                "api_routes": [],
                "file_index": [],
                "function_summaries": [],
                "class_summaries": [],
                "query_filters": [],
                "router_wiring": [],
                "stats": {
                    "python_files": 0,
                    "tables": 0,
                    "routes": 0,
                    "functions": 0,
                    "classes": 0,
                    "query_filters": 0,
                    "router_wiring": 0,
                },
                "index_meta": {
                    "folder_exists": False,
                    "root_sha256": "",
                },
            }
        indexed_at = datetime.now(UTC).isoformat()
        index["index_meta"] = {
            **(index.get("index_meta") or {}),
            "project_active": bool(project.is_active),
            "folder_exists": root.exists(),
            "folder_path": str(root),
            "indexed_at": indexed_at,
        }
        helper = await self.helpers.get_or_create(project.id)
        helper.database_schema = {
            **index["database_schema"],
            "query_filters": index.get("query_filters") or [],
            "class_summaries": index.get("class_summaries") or [],
            "router_wiring": index.get("router_wiring") or [],
            "index_meta": index.get("index_meta") or {},
        }
        helper.api_routes = index["api_routes"]
        helper.file_index = index["file_index"]
        helper.function_summaries = index["function_summaries"]
        helper.requirement_contracts = list(helper.requirement_contracts or [])[:20]
        stats = index["stats"]
        helper.project_context = (
            f"{project.name}: {project.description or 'Generated FastAPI project'}. "
            f"Indexed {stats['python_files']} Python files, {stats['tables']} SQLModel tables, "
            f"{stats['routes']} API routes, and {stats['functions']} functions."
        )[:2000]
        helper.recent_changes = ([
            {
                "kind": "index",
                "reason": reason,
                "stats": stats,
                "endpoint_doc": endpoint_doc,
                "index_meta": index["index_meta"],
            }
        ] + list(helper.recent_changes or []))[:20]
        await self.db.flush()
        logger.info("project.index refreshed project_id=%s stats=%s", str(project.id), stats)
        index["endpoint_doc"] = endpoint_doc
        index["requirement_contracts"] = list(helper.requirement_contracts or [])[:20]
        return index
