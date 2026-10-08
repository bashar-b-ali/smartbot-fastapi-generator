from __future__ import annotations

import ast
import json
import re
from contextlib import suppress
from time import perf_counter
from typing import Any

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.logging import logger
from app.llm.providers import Message, OllamaProvider
from app.services.model_pipeline.types import ModelJsonResponseError, ModelRunUsage
from app.services.project_edit.io import safe_rel

TRACE_EXCERPT_CHARS = 1800
OLLAMA_CONTEXT_BUCKETS = (4096, 8192, 16384, 32768, 65536, 131072)
DEFAULT_OLLAMA_CONTEXT = 8192
CONSERVATIVE_CHARS_PER_TOKEN = 3
CONTEXT_SAFETY_TOKENS = 512


def provider_label(provider: Any, *, suffix: str) -> str:
    model = str(getattr(provider, "model", "") or "").strip()
    return f"{provider.__class__.__name__.replace('Provider', '').lower()}-{suffix}/{model}".rstrip("/")


def json_kwargs(provider: Any) -> dict[str, Any]:
    provider_name = provider.__class__.__name__.lower()
    if isinstance(provider, OllamaProvider):
        return {"format": "json"}
    if "google" in provider_name or "gemini" in provider_name:
        return {"response_mime_type": "application/json"}
    if "openai" in provider_name:
        return {"response_format": {"type": "json_object"}}
    return {}


def adaptive_context_kwargs(provider: Any, *, system: str, user_payload: str, max_tokens: int) -> dict[str, Any]:
    if not isinstance(provider, OllamaProvider):
        return {}
    provider_limit = int(getattr(provider, "num_ctx", 0) or 0)
    if provider_limit <= 0:
        return {}
    estimated_prompt_tokens = max(1, (len(system) + len(user_payload)) // 4)
    needed = estimated_prompt_tokens + max_tokens + 512
    selected = provider_limit
    for bucket in OLLAMA_CONTEXT_BUCKETS:
        if needed <= bucket:
            selected = min(bucket, provider_limit)
            break
    kwargs: dict[str, Any] = {"num_ctx": max(2048, selected)}
    keep_alive = settings.ollama_pipeline_keep_alive or settings.ollama_keep_alive
    if keep_alive:
        kwargs["keep_alive"] = keep_alive
    return kwargs


def _compact_value(value: Any, *, string_limit: int, list_limit: int) -> Any:
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        half = max(80, string_limit // 2)
        return value[:half] + "\n...<context truncated>...\n" + value[-half:]
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, string_limit=string_limit, list_limit=list_limit)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = list(value)
        compacted = [
            _compact_value(item, string_limit=string_limit, list_limit=list_limit)
            for item in items[:list_limit]
        ]
        if len(items) > list_limit:
            compacted.append({"context_truncated_items": len(items) - list_limit})
        return compacted
    return value


def prepare_model_request(
    provider: Any,
    *,
    system: str,
    payload: dict[str, Any],
    max_tokens: int,
) -> tuple[str, int, dict[str, Any]]:
    context_limit = int(
        getattr(provider, "context_window", 0)
        or getattr(provider, "num_ctx", 0)
        or (DEFAULT_OLLAMA_CONTEXT if isinstance(provider, OllamaProvider) else 0)
    )
    if context_limit <= 0:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        return serialized, max_tokens, adaptive_context_kwargs(
            provider, system=system, user_payload=serialized, max_tokens=max_tokens
        )

    output_tokens = min(max_tokens, max(512, int(context_limit * 0.35)))
    available_prompt_tokens = max(
        512,
        context_limit - output_tokens - CONTEXT_SAFETY_TOKENS - max(1, len(system) // CONSERVATIVE_CHARS_PER_TOKEN),
    )
    max_payload_chars = available_prompt_tokens * CONSERVATIVE_CHARS_PER_TOKEN
    compacted: Any = payload
    serialized = json.dumps(compacted, ensure_ascii=False, default=str)
    string_limit = min(6000, max_payload_chars)
    list_limit = 20
    while len(serialized) > max_payload_chars and (string_limit > 300 or list_limit > 3):
        string_limit = max(300, string_limit // 2)
        list_limit = max(3, list_limit // 2)
        compacted = _compact_value(payload, string_limit=string_limit, list_limit=list_limit)
        serialized = json.dumps(compacted, ensure_ascii=False, default=str)
    if len(serialized) > max_payload_chars:
        excerpt_chars = max(200, max_payload_chars - 160)
        serialized = json.dumps(
            {
                "context_truncated": True,
                "original_chars": len(serialized),
                "payload_excerpt": serialized[:excerpt_chars],
            },
            ensure_ascii=False,
        )
    context_kwargs = adaptive_context_kwargs(
        provider, system=system, user_payload=serialized, max_tokens=output_tokens
    )
    return serialized, output_tokens, context_kwargs


def extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValidationError("model did not return a JSON object") from exc
        candidate = raw[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as inner:
            # Tolerate safe Python-style dicts emitted by small local models.
            try:
                parsed = ast.literal_eval(candidate)
            except (SyntaxError, ValueError, TypeError):
                raise ValidationError("model returned invalid JSON") from inner
    if not isinstance(parsed, dict):
        raise ValidationError("model JSON must be an object")
    return parsed


def redact_trace_text(value: str, *, limit: int = TRACE_EXCERPT_CHARS) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|jwt[_-]?secret)"
        r"(\s*[:=]\s*)(['\"]?)[^'\"\s,;)}]+",
        r"\1\2\3<redacted>",
        text,
    )
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...<truncated {len(text) - limit} chars>"


def summarize_trace_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        if isinstance(value, (dict, list, tuple)):
            return {"type": type(value).__name__, "items": len(value)}
        if isinstance(value, str):
            return {"type": "str", "chars": len(value), "excerpt": redact_trace_text(value, limit=160)}
        return value
    if isinstance(value, dict):
        return {
            str(key): summarize_trace_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, (list, tuple)):
        return {
            "type": "list",
            "items": len(value),
            "sample": [summarize_trace_value(item, depth=depth + 1) for item in list(value)[:8]],
        }
    if isinstance(value, str):
        return {"type": "str", "chars": len(value), "excerpt": redact_trace_text(value, limit=220)}
    return value


def record_trace_event(trace: list[dict[str, Any]] | None, event: dict[str, Any]) -> None:
    clean = {key: value for key, value in event.items() if value is not None}
    if trace is not None:
        trace.append(clean)
    with suppress(Exception):
        logger.info("pipeline.stage", **clean)


def complete_json(
    provider: Any,
    system: str,
    payload: dict[str, Any],
    *,
    max_tokens: int = 3000,
    temperature: float = 0.05,
    trace: list[dict[str, Any]] | None = None,
    stage: str = "model.json",
    payload_summary: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ModelRunUsage]:
    user_payload, max_tokens, context_kwargs = prepare_model_request(
        provider, system=system, payload=payload, max_tokens=max_tokens
    )
    started = perf_counter()
    usage = ModelRunUsage()
    try:
        response = provider.complete(
            [
                Message(role="system", content=system),
                Message(role="user", content=user_payload),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **context_kwargs,
            **json_kwargs(provider),
        )
    except Exception as exc:
        record_trace_event(
            trace,
            {
                "stage": stage,
                "status": "provider_error",
                "provider": provider_label(provider, suffix="pipeline"),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "max_tokens": max_tokens,
                **context_kwargs,
                "temperature": temperature,
                "payload": payload_summary or summarize_trace_value(payload),
                "system_excerpt": redact_trace_text(system, limit=600),
                "error": str(exc),
            },
        )
        raise
    usage.add_response(response)
    raw = str(response.content or "")
    try:
        parsed = extract_json_object(raw)
    except ValidationError as exc:
        record_trace_event(
            trace,
            {
                "stage": stage,
                "status": "parse_error",
                "provider": provider_label(provider, suffix="pipeline"),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "max_tokens": max_tokens,
                **context_kwargs,
                "temperature": temperature,
                "payload": payload_summary or summarize_trace_value(payload),
                "system_excerpt": redact_trace_text(system, limit=600),
                "raw_response_excerpt": redact_trace_text(raw),
                "raw_response_chars": len(raw),
                "error": str(exc),
            },
        )
        raise ModelJsonResponseError(str(exc), raw_content=raw, usage=usage) from exc
    record_trace_event(
        trace,
        {
            "stage": stage,
            "status": "ok",
            "provider": provider_label(provider, suffix="pipeline"),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "max_tokens": max_tokens,
            **context_kwargs,
            "temperature": temperature,
            "payload": payload_summary or summarize_trace_value(payload),
            "system_excerpt": redact_trace_text(system, limit=600),
            "raw_response_excerpt": redact_trace_text(raw),
            "raw_response_chars": len(raw),
            "parsed_keys": sorted(str(key) for key in parsed)[:30],
        },
    )
    return parsed, usage


def complete_json_with_repair(
    provider: Any,
    system: str,
    payload: dict[str, Any],
    *,
    required_keys: list[str],
    stage: str,
    trace: list[dict[str, Any]] | None = None,
    max_tokens: int = 3000,
    repair_max_tokens: int | None = None,
    temperature: float = 0.05,
) -> tuple[dict[str, Any], ModelRunUsage]:
    try:
        return complete_json(
            provider,
            system,
            payload,
            max_tokens=max_tokens,
            temperature=temperature,
            trace=trace,
            stage=stage,
        )
    except ModelJsonResponseError as exc:
        usage = exc.usage
        repair_system = (
            "Repair the previous model response into exactly one valid JSON object. "
            "Do not add prose, markdown, code fences, comments, or explanations. "
            f"The JSON object must include these top-level keys: {', '.join(required_keys)}."
        )
        repaired, repair_usage = complete_json(
            provider,
            repair_system,
            {
                "original_task_system": system,
                "original_payload": payload,
                "invalid_response_excerpt": redact_trace_text(exc.raw_content, limit=4000),
                "parse_error": str(exc),
                "required_keys": required_keys,
            },
            max_tokens=repair_max_tokens or max_tokens,
            temperature=0.0,
            trace=trace,
            stage=f"{stage}_json_repair",
        )
        usage.add_usage(repair_usage)
        usage.retries += 1
        return repaired, usage


def complete_text(
    provider: Any,
    system: str,
    payload: dict[str, Any],
    *,
    max_tokens: int = 9000,
    temperature: float = 0.05,
    trace: list[dict[str, Any]] | None = None,
    stage: str = "model.text",
    payload_summary: dict[str, Any] | None = None,
) -> tuple[str, ModelRunUsage]:
    user_payload, max_tokens, context_kwargs = prepare_model_request(
        provider, system=system, payload=payload, max_tokens=max_tokens
    )
    started = perf_counter()
    usage = ModelRunUsage()
    try:
        response = provider.complete(
            [
                Message(role="system", content=system),
                Message(role="user", content=user_payload),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            **context_kwargs,
        )
    except Exception as exc:
        record_trace_event(
            trace,
            {
                "stage": stage,
                "status": "provider_error",
                "provider": provider_label(provider, suffix="pipeline"),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "max_tokens": max_tokens,
                **context_kwargs,
                "temperature": temperature,
                "payload": payload_summary or summarize_trace_value(payload),
                "system_excerpt": redact_trace_text(system, limit=600),
                "error": str(exc),
            },
        )
        raise
    usage.add_response(response)
    raw = str(response.content or "")
    wrapped_paths = re.findall(r"(?m)^###\s*FILE:\s*(.*?)\s*###\s*$", raw)
    record_trace_event(
        trace,
        {
            "stage": stage,
            "status": "ok",
            "provider": provider_label(provider, suffix="pipeline"),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "max_tokens": max_tokens,
            **context_kwargs,
            "temperature": temperature,
            "payload": payload_summary or summarize_trace_value(payload),
            "system_excerpt": redact_trace_text(system, limit=600),
            "raw_response_excerpt": redact_trace_text(raw),
            "raw_response_chars": len(raw),
            "wrapped_files": [safe_rel(path) for path in wrapped_paths[:30] if path.strip()],
        },
    )
    return raw, usage
