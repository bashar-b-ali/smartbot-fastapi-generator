"""Bridges the synchronous FastAPI LLM pipeline to async FastAPI handlers.

The pipeline does blocking HTTP calls to LLM providers, so every entry point runs
inside `asyncio.to_thread`. Per-request `model_id` resolves to a fresh pipeline so
you can switch models without sharing state across requests.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.exceptions import ValidationError
from app.llm.pipeline import FastAPIBotPipeline, PipelineConfig
from app.models.llm_config import LLMModelConfig
from app.repositories.llm_config import LLMModelConfigRepository
from app.services.project_context import CHAT_CONTEXT_MAX_CHARS

CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


def clean_chat_response(text: str) -> str:
    cleaned = CODE_BLOCK_RE.sub(
        "[Code omitted from chat. Generated code is written directly to project files.]",
        text or "",
    ).strip()
    if len(cleaned) > 2500:
        cleaned = cleaned[:2400].rstrip() + "\n\n[Response shortened. Ask to view a specific file for details.]"
    return cleaned


@dataclass
class _ModelLike:
    """Adapter so the LLM pipeline can read attributes off our SQLAlchemy row.

    We pass an instance shaped like the SQLAlchemy row rather than the row itself
    so the pipeline does not hold a session-bound object in a worker thread.
    """

    id: UUID
    name: str
    provider: str
    model_id: str
    api_key: str
    api_base_url: str
    default_max_tokens: int
    temperature: float
    input_cost_per_million: float
    output_cost_per_million: float

    @classmethod
    def from_orm(cls, m: LLMModelConfig) -> _ModelLike:
        return cls(
            id=m.id,
            name=m.name,
            provider=m.provider,
            model_id=m.model_id,
            api_key=decrypt_secret(m.api_key),
            api_base_url=m.api_base_url,
            default_max_tokens=m.default_max_tokens,
            temperature=m.temperature,
            input_cost_per_million=m.input_cost_per_million,
            output_cost_per_million=m.output_cost_per_million,
        )


class LLMService:
    def __init__(self, db: AsyncSession, user_id: UUID | None = None):
        self.db = db
        self.user_id = user_id
        self.repo = LLMModelConfigRepository(db)

    async def _resolve_config(self, model_id: UUID | None) -> _ModelLike | None:
        if model_id:
            row = (
                await self.repo.get_active_for_user(model_id, self.user_id)
                if self.user_id
                else await self.repo.get_active(model_id)
            )
            if row:
                return _ModelLike.from_orm(row)
            raise ValidationError("Model config not found or inactive")
        if self.user_id:
            default = await self.repo.get_default(self.user_id)
            return _ModelLike.from_orm(default) if default else None
        return None

    async def _pipeline(self, model_id: UUID | None = None) -> FastAPIBotPipeline:
        cfg = await self._resolve_config(model_id)
        return FastAPIBotPipeline(
            PipelineConfig(max_context_length=CHAT_CONTEXT_MAX_CHARS),
            model_config=cfg,
        )

    async def analyze(
        self, description: str, *, context: str | None = None, model_id: UUID | None = None
    ) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        result = await asyncio.to_thread(p.analyze_requirements_dict, description, context)
        return {"result": result, "tokens": _tokens(p), "model": _model_name(p)}

    async def chat(
        self,
        message: str,
        *,
        history: list[dict[str, str]] | None = None,
        project_context: str | None = None,
        model_id: UUID | None = None,
    ) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        text = await asyncio.to_thread(p.chat, message, project_context, history or [])
        return {"text": clean_chat_response(text), "tokens": _tokens(p), "model": _model_name(p)}

    async def review_code(
        self, code: str, *, context: str | None = None, model_id: UUID | None = None
    ) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        result = await asyncio.to_thread(p.review_code, code, context)
        return {"result": result, "tokens": _tokens(p), "model": _model_name(p)}

    async def fix_error(
        self,
        error: str,
        code: str,
        *,
        traceback: str | None = None,
        context: str | None = None,
        model_id: UUID | None = None,
    ) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        result = await asyncio.to_thread(p.fix_error, error, code, traceback, context)
        return {"result": result, "tokens": _tokens(p), "model": _model_name(p)}

    async def explain_code(
        self, code: str, *, model_id: UUID | None = None
    ) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        text = await asyncio.to_thread(p.explain_code, code)
        return {"text": text, "tokens": _tokens(p), "model": _model_name(p)}

    async def model_info(self, model_id: UUID | None = None) -> dict[str, Any]:
        p = await self._pipeline(model_id)
        return await asyncio.to_thread(p.get_model_info)

    async def health_check(self) -> bool:
        p = await self._pipeline(None)
        return await asyncio.to_thread(p.health_check)


def _tokens(p: FastAPIBotPipeline) -> int:
    return p.usage.total_input_tokens + p.usage.total_output_tokens


def _model_name(p: FastAPIBotPipeline) -> str:
    info = p.get_model_info()
    return f"{info.get('provider', '')}/{info.get('model', '')}".strip("/")
