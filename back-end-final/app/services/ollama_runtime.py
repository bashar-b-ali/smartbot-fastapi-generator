from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import logger
from app.llm.providers import OllamaProvider


def _model_name(model: Any) -> str:
    if isinstance(model, dict):
        return str(model.get("model") or model.get("name") or "")
    return str(getattr(model, "model", "") or getattr(model, "name", "") or "")


def _model_aliases(model: str) -> set[str]:
    normalized = model.strip().lower()
    if not normalized:
        return set()
    aliases = {normalized}
    if ":" not in normalized.rsplit("/", 1)[-1]:
        aliases.add(f"{normalized}:latest")
    return aliases


def _is_model_installed(model: str, installed: set[str]) -> bool:
    installed_aliases = set()
    for item in installed:
        installed_aliases.update(_model_aliases(item))
    return bool(_model_aliases(model) & installed_aliases)


def _warm_server_model_sync() -> None:
    model = settings.llm_model or OllamaProvider.DEFAULT_MODEL
    provider = OllamaProvider(model=model, keep_alive=settings.ollama_keep_alive)
    if not provider._client:
        logger.warning("ollama_warmup_skipped", reason="client_not_initialized")
        return

    response = provider._client.list()
    models = getattr(response, "models", None)
    if models is None and isinstance(response, dict):
        models = response.get("models", [])
    installed = {_model_name(m) for m in models or []}

    if not _is_model_installed(model, installed):
        logger.warning(
            "ollama_model_missing",
            model=model,
            installed=sorted(m for m in installed if m),
            hint="Install the model manually; this backend never pulls Ollama models automatically.",
        )
        return

    provider._client.generate(
        model=model,
        prompt="ok",
        options={"num_predict": 1},
        keep_alive=settings.ollama_keep_alive,
    )
    logger.info("ollama_model_ready", model=model, keep_alive=settings.ollama_keep_alive)


async def warm_server_ollama_model() -> None:
    if not settings.ollama_auto_warmup:
        return
    if (settings.llm_provider or "ollama").lower() != "ollama":
        return
    try:
        await asyncio.to_thread(_warm_server_model_sync)
    except Exception as exc:
        logger.warning("ollama_warmup_failed", error=str(exc))
