"""Compact conversation state for project generation and edits.

This module does not infer fields, routes, predefined modules, or implementation
steps. It only packages recent user messages so the model receives the edit
request with enough conversational context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChangeRequestState:
    current_message: str
    user_messages: list[str]
    resolved_prompt: str
    summary: str
    confirmation: bool = False

    @property
    def has_context(self) -> bool:
        return len(self.user_messages) > 1


def build_change_request_state(history_rows: list[Any], current_message: str) -> ChangeRequestState:
    previous = [
        str(getattr(row, "content", "") or "").strip()
        for row in history_rows
        if getattr(row, "message_type", "") == "user" and str(getattr(row, "content", "") or "").strip()
    ]
    messages = (previous + [(current_message or "").strip()])[-10:]
    confirmation = _is_confirmation(current_message)
    summary = f"{len(messages)} recent user messages"
    resolved_prompt = _resolved_prompt(messages, summary)
    return ChangeRequestState(
        current_message=(current_message or "").strip(),
        user_messages=messages,
        resolved_prompt=resolved_prompt,
        summary=summary,
        confirmation=confirmation,
    )


def _is_confirmation(message: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(confirm|confirmed|go ahead|do it|apply|execute|proceed|continue|yes|ok|okay|start)\s*[.!]?\s*",
            message or "",
            flags=re.IGNORECASE,
        )
    )


def _resolved_prompt(messages: list[str], summary: str) -> str:
    lines = [
        "Backend change request derived from recent conversation.",
        f"Context summary - {summary}",
        "",
        "User-authored request messages follow.",
    ]
    for idx, message in enumerate(messages, start=1):
        lines.append(f"User message {idx} - {message[:900]}")
    return "\n".join(lines)
