"""FastAPI assistant pipeline with dynamic model selection and token budgeting."""

import json
import logging
import os
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from .parsers import ParsedAnalysis, ResponseParser
from .prompts import TASK_BUDGETS, PromptManager, TaskType
from .providers import LLMProvider, LLMResponse, Message, get_provider, get_provider_from_config

logger = logging.getLogger(__name__)


def _config_value(env_name: str, settings_attr: str) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    try:
        from app.core.config import settings
    except Exception:
        return ""
    return str(getattr(settings, settings_attr, "") or "")


@dataclass
class PipelineConfig:
    provider_type: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float = 0.7
    max_tokens: int = 2048
    max_retries: int = 3
    retry_delay: float = 1.0
    max_chat_history: int = 3
    max_context_length: int = 500
    track_usage: bool = True

    def __post_init__(self):
        if not self.provider_type:
            self.provider_type = _config_value("LLM_PROVIDER", "llm_provider") or None
        if not self.model:
            self.model = _config_value("LLM_MODEL", "llm_model") or None


@dataclass
class UsageStats:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    total_cost: float = 0.0
    requests_by_task: dict[str, int] = field(default_factory=dict)

    def add_response(self, response: LLMResponse, task_type: str = "unknown"):
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.total_requests += 1
        self.total_cost += response.cost_estimate
        self.requests_by_task[task_type] = self.requests_by_task.get(task_type, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "total_requests": self.total_requests,
            "estimated_cost_usd": round(self.total_cost, 6),
            "requests_by_task": self.requests_by_task,
        }


def with_retry(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff
            logger.error(f"All {max_retries} attempts failed")
            raise last_exception
        return wrapper
    return decorator


class FastAPIBotPipeline:
    """
    Main pipeline with:
    - Dynamic model selection (from DB config or env)
    - Per-task token budgets
    - Cost tracking with DB rates
    """

    def __init__(self, config: PipelineConfig | None = None, model_config=None):
        """
        Args:
            config: Pipeline config (env-based fallback)
            model_config: LLMModelConfig DB instance (takes priority)
        """
        self.config = config or PipelineConfig()
        self._model_config = model_config
        self._provider: LLMProvider | None = None
        self._usage = UsageStats()
        self._initialized = False

    @property
    def provider(self) -> LLMProvider:
        if not self._provider:
            if self._model_config:
                self._provider = get_provider_from_config(self._model_config)
                logger.info(f"Using DB model: {self._model_config.name}")
            else:
                self._provider = get_provider(
                    provider_type=self.config.provider_type,
                    api_key=self.config.api_key,
                    model=self.config.model
                )
            self._initialized = True
            logger.info(f"Initialized provider: {type(self._provider).__name__} / {self._provider.model}")
        return self._provider

    @property
    def usage(self) -> UsageStats:
        return self._usage

    def reset_usage(self):
        self._usage = UsageStats()

    # =========================================================================
    # CORE - with task-based token budgets
    # =========================================================================

    def _get_task_budget(self, task_type_str: str):
        """Get (max_tokens, temperature) for a task type."""
        try:
            tt = TaskType(task_type_str)
        except ValueError:
            return self.config.max_tokens, self.config.temperature

        budget = TASK_BUDGETS.get(tt)
        if budget:
            max_tok, temp = budget
            # If DB model has a cap, respect it
            if self._model_config and self._model_config.default_max_tokens:
                max_tok = min(max_tok, self._model_config.default_max_tokens)
            return max_tok, temp
        return self.config.max_tokens, self.config.temperature

    def _call_llm(
        self,
        messages: list[Message],
        task_type: str = "unknown",
        temperature: float | None = None,
        max_tokens: int | None = None
    ) -> LLMResponse:
        budget_tokens, budget_temp = self._get_task_budget(task_type)

        @with_retry(max_retries=self.config.max_retries, delay=self.config.retry_delay)
        def _make_call():
            return self.provider.complete(
                messages=messages,
                temperature=temperature if temperature is not None else budget_temp,
                max_tokens=max_tokens or budget_tokens
            )

        response = _make_call()

        # Attach DB cost rates if available
        if self._model_config:
            response._input_cost_rate = self._model_config.input_cost_per_million
            response._output_cost_rate = self._model_config.output_cost_per_million

        if self.config.track_usage:
            self._usage.add_response(response, task_type)

        return response

    def _build_messages(self, task_type: TaskType, **kwargs) -> list[Message]:
        prompt = PromptManager.get_prompt(task_type, **kwargs)
        return [
            Message(role="system", content=prompt.system),
            Message(role="user", content=prompt.user)
        ]

    # =========================================================================
    # PROJECT ANALYSIS
    # =========================================================================

    def analyze_requirements(self, description: str, context: str | None = None) -> ParsedAnalysis:
        logger.info("Analyzing project requirements...")
        messages = self._build_messages(
            TaskType.ANALYZE,
            description=description,
            context=context or ""
        )
        response = self._call_llm(messages, task_type="analyze")
        analysis = ResponseParser.parse_analysis(response.content)
        logger.info(f"Analysis complete. Found {len(analysis.api_endpoints)} endpoints")
        return analysis

    def analyze_requirements_dict(self, description: str, context: str | None = None) -> dict[str, Any]:
        analysis = self.analyze_requirements(description, context)
        return {
            'database_schema': (
                analysis.database_schema.get('raw', '')
                if isinstance(analysis.database_schema, dict)
                else json.dumps(analysis.database_schema, indent=2)
            ),
            'api_endpoints': analysis.api_endpoints,
            'technical_specs': (
                analysis.technical_specs.get('raw', '')
                if isinstance(analysis.technical_specs, dict)
                else json.dumps(analysis.technical_specs, indent=2)
            ),
        }

    # =========================================================================
    # CODE GENERATION
    # =========================================================================

    def chat(
        self, message: str,
        project_context: str | None = None,
        chat_history: list[dict[str, str]] | None = None
    ) -> str:
        # Truncate context to save tokens
        ctx = project_context or "No project context."
        if len(ctx) > self.config.max_context_length:
            ctx = ctx[:self.config.max_context_length] + "..."

        history_str = PromptManager.format_chat_history(
            chat_history or [],
            max_messages=self.config.max_chat_history
        )
        messages = self._build_messages(
            TaskType.CHAT,
            message=message,
            project_context=ctx,
            chat_history=history_str
        )
        response = self._call_llm(messages, task_type="chat")
        return response.content

    def chat_stream(
        self, message: str,
        project_context: str | None = None,
        chat_history: list[dict[str, str]] | None = None
    ) -> Generator[str, None, None]:
        ctx = project_context or "No project context."
        if len(ctx) > self.config.max_context_length:
            ctx = ctx[:self.config.max_context_length] + "..."

        history_str = PromptManager.format_chat_history(
            chat_history or [],
            max_messages=self.config.max_chat_history
        )
        prompt = PromptManager.get_prompt(
            TaskType.CHAT,
            message=message,
            project_context=ctx,
            chat_history=history_str
        )
        messages = [
            Message(role="system", content=prompt.system),
            Message(role="user", content=prompt.user)
        ]

        budget_tokens, budget_temp = self._get_task_budget("chat")
        yield from self.provider.stream(
            messages=messages,
            temperature=budget_temp,
            max_tokens=budget_tokens
        )

    # =========================================================================
    # CODE REVIEW & FIX
    # =========================================================================

    def review_code(self, code: str, context: str | None = None) -> dict[str, Any]:
        messages = self._build_messages(
            TaskType.REVIEW_CODE,
            code=code,
            context=context or ""
        )
        response = self._call_llm(messages, task_type="review_code")
        return ResponseParser.parse_code_review(response.content)

    def fix_error(self, error, code, traceback=None, context=None) -> dict[str, Any]:
        messages = self._build_messages(
            TaskType.FIX_ERROR,
            error=error,
            code=code,
            traceback=traceback or "No traceback",
            context=context or ""
        )
        response = self._call_llm(messages, task_type="fix_error")
        return ResponseParser.parse_error_fix(response.content)

    def explain_code(self, code: str) -> str:
        messages = self._build_messages(TaskType.EXPLAIN, code=code)
        response = self._call_llm(messages, task_type="explain")
        return response.content

    # =========================================================================
    # UTILITY
    # =========================================================================

    def health_check(self) -> bool:
        try:
            return self.provider.health_check()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def get_model_info(self) -> dict[str, Any]:
        info = {
            "provider": type(self.provider).__name__,
            "model": self.provider.model,
            "initialized": self._initialized,
            "usage": self._usage.to_dict() if self.config.track_usage else None
        }
        if self._model_config:
            info["model_config_id"] = str(self._model_config.id)
            info["model_config_name"] = self._model_config.name
        return info

    def get_last_response_tokens(self) -> dict[str, int]:
        """Return token counts from the last request (for saving to ChatMessage)."""
        return {
            "input_tokens": self._usage.total_input_tokens,
            "output_tokens": self._usage.total_output_tokens,
            "total_requests": self._usage.total_requests,
        }


# Convenience functions
_default_pipeline: FastAPIBotPipeline | None = None


def get_pipeline(config: PipelineConfig | None = None) -> FastAPIBotPipeline:
    global _default_pipeline
    if _default_pipeline is None or config is not None:
        _default_pipeline = FastAPIBotPipeline(config)
    return _default_pipeline


def analyze(description: str, context: str = None) -> ParsedAnalysis:
    return get_pipeline().analyze_requirements(description, context)




def chat(message: str, context: str = None, history: list = None) -> str:
    return get_pipeline().chat(message, context, history)
