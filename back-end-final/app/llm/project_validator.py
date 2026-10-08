"""Static validation for generated FastAPI project files."""
from __future__ import annotations

import ast
import builtins
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.llm.writer import WriteOutcome
from app.services.model_pipeline.profiles import FASTAPI_PROFILE


@dataclass(frozen=True)
class FileValidation:
    path: str
    passed: bool
    error: str = ""


@dataclass(frozen=True)
class ProjectValidation:
    passed: bool
    checks: list[FileValidation] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [check.__dict__ for check in self.checks],
            "missing_required": self.missing_required,
        }


def _module_exists(project_root: Path, module: str) -> bool:
    rel = Path(*module.split("."))
    return (
        (project_root / f"{rel.as_posix()}.py").is_file()
        or (project_root / rel / "__init__.py").is_file()
    )


def _local_import_errors(project_root: Path, rel_path: str, tree: ast.AST) -> list[str]:
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


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _decorator_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _has_route_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "websocket", "api_route"}
    for decorator in node.decorator_list:
        name = _decorator_name(decorator).lower()
        if any(name.endswith(f".{method}") for method in methods):
            return True
    return False


def _is_model_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    stem = Path(rel_path).stem
    return name in {"model.py", "models.py", "db_models.py"} or stem.endswith("_model") or stem.endswith("_models")


def _is_schema_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    stem = Path(rel_path).stem
    return name in {"schema.py", "schemas.py"} or stem.endswith("_schema") or stem.endswith("_schemas")


def _is_placeholder_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [item for item in node.body if not isinstance(item, ast.Expr) or not isinstance(item.value, ast.Constant)]
    if not body:
        return True
    return all(isinstance(item, (ast.Pass, ast.Expr)) and (
        isinstance(item, ast.Pass)
        or (isinstance(item.value, ast.Constant) and item.value.value is Ellipsis)
    ) for item in body)


def _file_role_errors(rel_path: str, source: str, tree: ast.AST) -> list[str]:
    errors: list[str] = []
    app_instantiations: list[int] = []
    route_handlers: list[str] = []
    placeholders: list[str] = []
    has_db_session_factory = False
    has_database_engine = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = _decorator_name(node.func)
            if func_name in {"FastAPI", "fastapi.FastAPI"} or func_name.endswith(".FastAPI"):
                app_instantiations.append(getattr(node, "lineno", 0))
            if func_name.endswith("create_engine"):
                has_database_engine = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in {"get_db", "get_session"}:
                has_db_session_factory = True
            if _has_route_decorator(node):
                route_handlers.append(node.name)
            if _is_placeholder_function(node):
                placeholders.append(node.name)

    lower_source = source.lower()
    if "todo" in lower_source or "implement logic" in lower_source or "implement " in lower_source:
        errors.append("contains placeholder implementation text")
    if placeholders:
        errors.append("contains placeholder function bodies: " + ", ".join(placeholders[:8]))
    if rel_path != FASTAPI_PROFILE.entrypoint_file and app_instantiations:
        errors.append("non-entrypoint file instantiates FastAPI application")
    if rel_path == FASTAPI_PROFILE.database_file:
        if route_handlers:
            errors.append("database file contains route handlers")
        if app_instantiations:
            errors.append("database file contains FastAPI app setup")
        if not (has_database_engine and has_db_session_factory):
            errors.append("database file must define engine setup and get_db/get_session")
    if rel_path == FASTAPI_PROFILE.entrypoint_file and len(app_instantiations) != 1:
        errors.append("entrypoint must instantiate exactly one FastAPI application")
    if _is_model_file(rel_path) and route_handlers:
        errors.append("model file contains route handlers")
    if _is_schema_file(rel_path) and route_handlers:
        errors.append("schema file contains route handlers")
    return errors


def _bound_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            names.update(_bound_names(item))
    return names


def _loaded_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def _target_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    names: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Param)):
            names.add(item.id)
    return names


class _FunctionBodyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)


def _walk_function_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    visitor = _FunctionBodyVisitor()
    for child in node.body:
        visitor.visit(child)
    return visitor.nodes


def _function_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
    for child in _walk_function_body(node):
        if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in targets:
                names.update(_target_names(target))
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            names.update(_target_names(child.target))
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                names.update(_target_names(item.optional_vars))
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, ast.Import):
            for alias in child.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for generator in child.generators:
                names.update(_target_names(generator.target))
    return names


def _module_defined_names(tree: ast.AST) -> set[str]:
    defined = set(dir(builtins))
    defined.update({"None", "True", "False"})
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                defined.update(_bound_names(target))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined.add((alias.asname or alias.name.split(".", 1)[0]).strip())
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    defined.add(alias.asname or alias.name)
    return defined


def _signature_reference_errors(rel_path: str, tree: ast.AST) -> list[str]:
    defined = _module_defined_names(tree)
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        referenced: set[str] = set()
        for decorator in node.decorator_list:
            referenced.update(_loaded_names(decorator))
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            referenced.update(_loaded_names(default))
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            referenced.update(_loaded_names(arg.annotation))
        referenced.update(_loaded_names(node.returns))
        missing = sorted(name for name in referenced if name not in defined)
        if missing:
            errors.append(f"{rel_path}:{node.name} references undefined names: {', '.join(missing[:8])}")
    return errors


def _body_reference_errors(rel_path: str, tree: ast.AST) -> list[str]:
    module_defined = _module_defined_names(tree)
    errors: list[str] = []
    ignored = {"self", "cls"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_names = _function_local_names(node)
        loaded = {
            child.id
            for child in _walk_function_body(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        missing = sorted(loaded - local_names - module_defined - ignored)
        if missing:
            errors.append(f"{rel_path}:{node.name} body references undefined names: {', '.join(missing[:8])}")
    return errors


def validate_written_project(root: str | Path, outcomes: Iterable[WriteOutcome]) -> ProjectValidation:
    """Check required files, parse Python, and catch unresolved local imports."""
    project_root = Path(root)
    paths = [outcome.path for outcome in outcomes]
    missing = [
        required
        for required in FASTAPI_PROFILE.required_files
        if not (project_root / required).is_file()
    ]

    checks: list[FileValidation] = []
    candidate_paths = sorted(
        {
            path.relative_to(project_root).as_posix()
            for path in project_root.rglob("*.py")
            if "__pycache__" not in path.parts
        }
        | {path for path in paths if path.endswith(".py")}
    )
    for rel_path in candidate_paths:
        if not rel_path.endswith(".py"):
            continue
        target = project_root / rel_path
        if not target.is_file():
            checks.append(FileValidation(path=rel_path, passed=False, error="file is missing"))
            continue
        try:
            source = target.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(target))
            import_errors = _local_import_errors(project_root, rel_path, tree)
            role_errors = _file_role_errors(rel_path, source, tree)
            reference_errors = _signature_reference_errors(rel_path, tree)
            body_reference_errors = _body_reference_errors(rel_path, tree)
            errors = [*import_errors, *role_errors, *reference_errors, *body_reference_errors]
            if errors:
                checks.append(FileValidation(path=rel_path, passed=False, error="; ".join(errors)))
            else:
                checks.append(FileValidation(path=rel_path, passed=True))
        except SyntaxError as exc:
            checks.append(FileValidation(path=rel_path, passed=False, error=str(exc)))

    passed = not missing and all(check.passed for check in checks)
    return ProjectValidation(passed=passed, checks=checks, missing_required=missing)
