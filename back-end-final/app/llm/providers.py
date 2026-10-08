"""
LLM Provider Abstraction Layer
Supports: OpenAI, Anthropic, Gemini/Google, Ollama, and DB-managed model configs
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from app.core.crypto import decrypt_secret
from app.core.exceptions import ValidationError

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


def _config_int(env_name: str, settings_attr: str) -> int:
    value = os.getenv(env_name)
    if value:
        try:
            return int(value)
        except ValueError:
            return 0
    try:
        from app.core.config import settings
    except Exception:
        return 0
    try:
        return int(getattr(settings, settings_attr, 0) or 0)
    except (TypeError, ValueError):
        return 0


class ModelType(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    GOOGLE = "google"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    latency_ms: float = 0
    raw_response: dict[str, Any] | None = None
    # DB-driven cost rates (set by pipeline when using LLMModelConfig)
    _input_cost_rate: float = 0.0
    _output_cost_rate: float = 0.0

    @property
    def cost_estimate(self) -> float:
        # Use DB rates if set
        if self._input_cost_rate or self._output_cost_rate:
            return (
                (self.input_tokens / 1_000_000) * self._input_cost_rate +
                (self.output_tokens / 1_000_000) * self._output_cost_rate
            )

        # Fallback: hardcoded pricing
        pricing = {
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4-turbo": (10.00, 30.00),
            "gpt-3.5-turbo": (0.50, 1.50),
            "claude-3-5-sonnet": (3.00, 15.00),
            "claude-3-opus": (15.00, 75.00),
            "claude-3-haiku": (0.25, 1.25),
            "gemini-2.5-flash": (0.30, 2.50),
            "gemini-2.0-flash": (0.10, 0.40),
            "gemini-pro": (0.50, 1.50),
        }

        for key, (inp, out) in pricing.items():
            if key in self.model.lower():
                return (
                    (self.input_tokens / 1_000_000) * inp +
                    (self.output_tokens / 1_000_000) * out
                )
        return 0.0


@dataclass
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMProvider(ABC):
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.model = model
        self._client = None

    @abstractmethod
    def complete(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 4096, **kwargs
    ) -> LLMResponse:
        pass

    @abstractmethod
    def stream(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 4096, **kwargs
    ) -> Generator[str, None, None]:
        pass

    def health_check(self) -> bool:
        try:
            response = self.complete(
                [Message(role="user", content="Say 'ok'")],
                max_tokens=10
            )
            return bool(response.content)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


class OpenAIProvider(LLMProvider):
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        super().__init__(
            api_key=api_key or _config_value("OPENAI_API_KEY", "openai_api_key"),
            model=model or self.DEFAULT_MODEL
        )
        self.base_url = base_url
        self._init_client()

    def _init_client(self):
        try:
            from openai import OpenAI
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        except ImportError:
            logger.warning("OpenAI package not installed. Run: pip install openai")
            self._client = None

    def complete(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("OpenAI client not initialized")

        start_time = time.time()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        latency = (time.time() - start_time) * 1000

        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=response.model,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
            finish_reason=response.choices[0].finish_reason or "",
            latency_ms=latency,
            raw_response=response.model_dump() if hasattr(response, 'model_dump') else None
        )

    def stream(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("OpenAI client not initialized")

        stream = self._client.chat.completions.create(
            model=self.model,
            messages=[m.to_dict() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class CustomOpenAICompatibleProvider(OpenAIProvider):
    """Provider for user-supplied OpenAI-compatible chat APIs."""

    DEFAULT_MODEL = "custom-model"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        if not base_url:
            raise RuntimeError("Custom provider requires api_base_url")
        super().__init__(
            api_key=api_key or "not-needed",
            model=model or self.DEFAULT_MODEL,
            base_url=base_url,
        )


class AnthropicProvider(LLMProvider):
    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key or _config_value("ANTHROPIC_API_KEY", "anthropic_api_key"),
            model=model or self.DEFAULT_MODEL
        )
        self._init_client()

    def _init_client(self):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            logger.warning("Anthropic package not installed. Run: pip install anthropic")
            self._client = None

    def complete(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Anthropic client not initialized")

        start_time = time.time()

        system_message = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_message = m.content
            else:
                chat_messages.append(m.to_dict())

        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_message if system_message else None,
            messages=chat_messages,
            temperature=temperature,
            **kwargs
        )
        latency = (time.time() - start_time) * 1000

        content = ""
        for block in response.content:
            if hasattr(block, 'text'):
                content += block.text

        return LLMResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            finish_reason=response.stop_reason or "",
            latency_ms=latency,
            raw_response=response.model_dump() if hasattr(response, 'model_dump') else None
        )

    def stream(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Anthropic client not initialized")

        system_message = ""
        chat_messages = []
        for m in messages:
            if m.role == "system":
                system_message = m.content
            else:
                chat_messages.append(m.to_dict())

        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system_message if system_message else None,
            messages=chat_messages,
            temperature=temperature,
            **kwargs
        ) as stream:
            yield from stream.text_stream


def _gemini_model_for_new_sdk(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


def _gemini_model_for_old_sdk(model: str) -> str:
    return model.removeprefix("models/")


def _gemini_prompt(messages: list[Message]) -> str:
    rendered: list[str] = []
    for message in messages:
        role = "SYSTEM" if message.role == "system" else message.role.upper()
        rendered.append(f"{role}:\n{message.content}")
    return "\n\n".join(rendered)


class GoogleProvider(LLMProvider):
    DEFAULT_MODEL = "models/gemini-2.5-flash"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        super().__init__(
            api_key=api_key or _config_value("GOOGLE_API_KEY", "google_api_key"),
            model=model or self.DEFAULT_MODEL
        )
        self._init_client()

    def _init_client(self):
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._client_kind = "google-genai"
            return
        except ImportError:
            pass

        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client = genai.GenerativeModel(_gemini_model_for_old_sdk(self.model))
            self._client_kind = "google-generativeai"
        except ImportError:
            logger.warning("Google AI package not installed. Run: pip install google-genai")
            self._client = None
            self._client_kind = ""

    def complete(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Google AI client not initialized")

        start_time = time.time()

        if self._client_kind == "google-genai":
            from google.genai import types

            config_kwargs: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if kwargs.get("response_mime_type"):
                config_kwargs["response_mime_type"] = kwargs["response_mime_type"]
            response = self._client.models.generate_content(
                model=_gemini_model_for_new_sdk(self.model),
                contents=_gemini_prompt(messages),
                config=types.GenerateContentConfig(**config_kwargs),
            )
            latency = (time.time() - start_time) * 1000
            usage = getattr(response, "usage_metadata", None)
            input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
            output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
            total_tokens = int(getattr(usage, "total_token_count", 0) or input_tokens + output_tokens)
            return LLMResponse(
                content=getattr(response, "text", "") or "",
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                finish_reason="stop",
                latency_ms=latency,
                raw_response=response.model_dump() if hasattr(response, "model_dump") else None,
            )

        import google.generativeai as genai

        history = []
        current_content = ""
        for m in messages:
            if m.role == "system":
                current_content = f"System Instructions: {m.content}\n\n"
            elif m.role == "user":
                history.append({"role": "user", "parts": [current_content + m.content]})
                current_content = ""
            elif m.role == "assistant":
                history.append({"role": "model", "parts": [m.content]})

        chat = self._client.start_chat(history=history[:-1] if len(history) > 1 else [])
        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        last_message = history[-1]["parts"][0] if history else ""
        response = chat.send_message(last_message, generation_config=generation_config)
        latency = (time.time() - start_time) * 1000

        return LLMResponse(
            content=response.text,
            model=self.model,
            input_tokens=response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
            output_tokens=response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
            total_tokens=response.usage_metadata.total_token_count if hasattr(response, 'usage_metadata') else 0,
            finish_reason="stop",
            latency_ms=latency
        )

    def stream(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Google AI client not initialized")

        if self._client_kind == "google-genai":
            from google.genai import types

            response = self._client.models.generate_content_stream(
                model=_gemini_model_for_new_sdk(self.model),
                contents=_gemini_prompt(messages),
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            for chunk in response:
                text = getattr(chunk, "text", "") or ""
                if text:
                    yield text
            return

        import google.generativeai as genai

        history = []
        current_content = ""
        for m in messages:
            if m.role == "system":
                current_content = f"System Instructions: {m.content}\n\n"
            elif m.role == "user":
                history.append({"role": "user", "parts": [current_content + m.content]})
                current_content = ""
            elif m.role == "assistant":
                history.append({"role": "model", "parts": [m.content]})

        chat = self._client.start_chat(history=history[:-1] if len(history) > 1 else [])
        generation_config = genai.GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        last_message = history[-1]["parts"][0] if history else ""
        response = chat.send_message(last_message, generation_config=generation_config, stream=True)

        for chunk in response:
            if chunk.text:
                yield chunk.text


GeminiProvider = GoogleProvider


class OllamaProvider(LLMProvider):
    DEFAULT_MODEL = "llama3.2"
    DEFAULT_HOST = "http://localhost:11434"

    def __init__(self, model=None, host=None, api_key=None, keep_alive=None, num_ctx: int | None = None):
        super().__init__(api_key=None, model=model or self.DEFAULT_MODEL)
        self.host = host or _config_value("OLLAMA_HOST", "ollama_host") or self.DEFAULT_HOST
        validate_local_ollama_host(self.host)
        self.keep_alive = (
            keep_alive
            or _config_value("OLLAMA_KEEP_ALIVE", "ollama_keep_alive")
            or "24h"
        )
        self.num_ctx = num_ctx if num_ctx is not None else _config_int("OLLAMA_NUM_CTX", "ollama_num_ctx")
        self._init_client()

    def _init_client(self):
        try:
            import ollama
            self._client = ollama.Client(host=self.host)
        except ImportError:
            logger.warning("Ollama package not installed. Run: pip install ollama")
            self._client = None

    def complete(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Ollama client not initialized")

        start_time = time.time()
        request_num_ctx = int(kwargs.pop("num_ctx", self.num_ctx) or 0)
        request_keep_alive = kwargs.pop("keep_alive", self.keep_alive)
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "keep_alive": request_keep_alive,
        }
        if request_num_ctx > 0:
            request["options"]["num_ctx"] = request_num_ctx
        if "format" in kwargs:
            request["format"] = kwargs["format"]
        response = self._client.chat(
            **request
        )
        latency = (time.time() - start_time) * 1000

        input_tokens = int(response.get("prompt_eval_count") or 0)
        output_tokens = int(response.get("eval_count") or 0)

        return LLMResponse(
            content=response['message']['content'],
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            finish_reason="stop",
            latency_ms=latency,
            raw_response=response
        )

    def stream(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        if not self._client:
            raise RuntimeError("Ollama client not initialized")

        request_num_ctx = int(kwargs.pop("num_ctx", self.num_ctx) or 0)
        request_keep_alive = kwargs.pop("keep_alive", self.keep_alive)
        stream = self._client.chat(
            model=self.model,
            messages=[m.to_dict() for m in messages],
            stream=True,
            options={
                "temperature": temperature,
                "num_predict": max_tokens,
                **({"num_ctx": request_num_ctx} if request_num_ctx > 0 else {}),
            },
            keep_alive=request_keep_alive,
        )
        for chunk in stream:
            if chunk['message']['content']:
                yield chunk['message']['content']


LOCAL_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def validate_local_ollama_host(host: str) -> None:
    parsed = urlparse(host if "://" in host else f"http://{host}")
    hostname = (parsed.hostname or "").lower()
    if hostname not in LOCAL_OLLAMA_HOSTS:
        raise ValidationError("Ollama provider is local-only; use localhost or 127.0.0.1.")


def provider_is_cloud(provider_type: str | None) -> bool:
    return (provider_type or "").lower() in {"openai", "anthropic", "gemini", "google", "custom"}


def provider_selection_metadata(
    provider: LLMProvider,
    *,
    requested_provider: str | None = None,
    requested_model: str | None = None,
    source: str = "server_default",
) -> dict[str, Any]:
    provider_name = provider.__class__.__name__.replace("Provider", "").lower()
    if isinstance(provider, CustomOpenAICompatibleProvider):
        provider_name = "custom"
    elif isinstance(provider, GoogleProvider):
        provider_name = "google"
    return {
        "requested_provider": requested_provider or "",
        "requested_model": requested_model or "",
        "resolved_provider": provider_name,
        "resolved_model": str(getattr(provider, "model", "") or ""),
        "source": source,
        "network_class": "local" if isinstance(provider, OllamaProvider) else "cloud",
    }

# Provider Registry
PROVIDERS = {
    ModelType.OPENAI: OpenAIProvider,
    ModelType.ANTHROPIC: AnthropicProvider,
    ModelType.GEMINI: GoogleProvider,
    ModelType.GOOGLE: GoogleProvider,
    ModelType.OLLAMA: OllamaProvider,
    ModelType.CUSTOM: CustomOpenAICompatibleProvider,
}


def _get_local_provider_class():
    """Lazy import to avoid loading torch at module level."""
    from .local_provider import LocalTransformerProvider
    return LocalTransformerProvider


def get_provider(
    provider_type: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    **kwargs
) -> LLMProvider:
    """
    Factory function to get an LLM provider.
    Auto-detects from environment if not specified.
    """
    if provider_type:
        try:
            p_type = ModelType(provider_type.lower())
        except ValueError as exc:
            raise ValueError(f"Unknown provider type: {provider_type}") from exc
        return PROVIDERS[p_type](api_key=api_key, model=model, **kwargs)

    logger.info("No provider selected, using Ollama (local)")
    return OllamaProvider(model=model, **kwargs)


def get_provider_from_config(model_config) -> LLMProvider:
    """
    Create a provider from an LLMModelConfig DB record.
    """
    provider_type = model_config.provider
    api_key = decrypt_secret(getattr(model_config, "api_key", "") or "") or None
    model_id = model_config.model_id

    # Handle local transformer models
    if provider_type == 'local':
        LocalProvider = _get_local_provider_class()
        return LocalProvider(model=model_id)

    kwargs = {}
    if model_config.api_base_url and provider_type == 'ollama':
        kwargs['host'] = model_config.api_base_url
    if model_config.api_base_url and provider_type in {'openai', 'custom'}:
        kwargs['base_url'] = model_config.api_base_url

    return get_provider(
        provider_type=provider_type,
        api_key=api_key,
        model=model_id,
        **kwargs
    )
