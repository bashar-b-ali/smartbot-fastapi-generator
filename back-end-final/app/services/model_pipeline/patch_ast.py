from __future__ import annotations

import ast


def insert_import(text: str, statement: str) -> str:
    statement = _clean_statement(statement)
    if not statement:
        return text
    if statement in _logical_lines(text):
        return text

    tree = ast.parse(text)
    lines = text.splitlines()
    insert_at = 0
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(getattr(tree.body[0], "value", None), ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        insert_at = int(getattr(tree.body[0], "end_lineno", tree.body[0].lineno))

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_at = int(getattr(node, "end_lineno", node.lineno))
        elif not (
            isinstance(node, ast.Expr)
            and isinstance(getattr(node, "value", None), ast.Constant)
            and isinstance(node.value.value, str)
        ):
            break

    lines.insert(insert_at, statement)
    return _join_lines(lines)


def insert_class_field(text: str, class_name: str, field_source: str) -> str:
    class_name = str(class_name or "").strip()
    field_source = _clean_statement(field_source)
    if not class_name or not field_source:
        return text
    if field_source in _logical_lines(text):
        return text

    tree = ast.parse(text)
    target = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
    if target is None:
        raise ValueError(f"class not found: {class_name}")

    insert_at = target.lineno
    for node in target.body:
        if isinstance(node, (ast.AnnAssign, ast.Assign)):
            insert_at = int(getattr(node, "end_lineno", node.lineno))
    indent = _line_indent(text.splitlines()[target.lineno - 1]) + "    "
    lines = text.splitlines()
    lines.insert(insert_at, f"{indent}{field_source.lstrip()}")
    return _join_lines(lines)


def insert_router_wiring(text: str, import_statement: str, include_statement: str) -> str:
    updated = insert_import(text, import_statement)
    include_statement = _clean_statement(include_statement)
    if not include_statement or include_statement in _logical_lines(updated):
        return updated

    tree = ast.parse(updated)
    lines = updated.splitlines()
    insert_at = len(lines)
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "include_router"
        ) or (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "FastAPI"
            and insert_at == len(lines)
        ):
            insert_at = int(getattr(node, "end_lineno", node.lineno))

    lines.insert(insert_at, include_statement)
    return _join_lines(lines)


def append_function(text: str, function_source: str) -> str:
    source = function_source.strip()
    if not source:
        return text
    ast.parse(source)
    if source in text:
        return text
    separator = "\n\n" if text.endswith("\n") else "\n\n\n"
    return f"{text.rstrip()}{separator}{source}\n"


def _clean_statement(value: str) -> str:
    return str(value or "").strip().rstrip()


def _logical_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip()}


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _join_lines(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"
