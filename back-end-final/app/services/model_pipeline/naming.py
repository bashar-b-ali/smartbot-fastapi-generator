from __future__ import annotations

import re
from typing import Any

NAME_ALIASES: dict[str, str] = {
    "costumer": "customer",
    "costumers": "customers",
}


def identifier(value: Any, *, numeric_prefix: str | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if not text:
        return ""

    parts = [NAME_ALIASES.get(part, part) for part in text.split("_") if part]
    text = "_".join(parts)
    text = NAME_ALIASES.get(text, text)

    if text and text[0].isdigit() and numeric_prefix:
        return f"{numeric_prefix}_{text}"
    return text


def singular(value: Any) -> str:
    value = identifier(value)
    irregular = {
        "analyses": "analysis",
        "statuses": "status",
    }
    if value in irregular:
        return irregular[value]
    if value.endswith("ies") and len(value) > 3:
        return f"{value[:-3]}y"
    if value.endswith(("sses", "xes", "zes", "ches", "shes")) and len(value) > 4:
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 1:
        return value[:-1]
    return value


def plural(value: Any) -> str:
    value = identifier(value)
    if not value:
        return ""
    if value.endswith("s"):
        return value
    if value.endswith("y") and len(value) > 1:
        return f"{value[:-1]}ies"
    return f"{value}s"


def class_name(value: Any, *, fallback: str = "") -> str:
    name = "".join(part[:1].upper() + part[1:] for part in singular(value).split("_") if part)
    return name or fallback
