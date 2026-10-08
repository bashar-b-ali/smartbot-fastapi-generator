from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.llm.providers import OllamaProvider, get_provider


def test_ollama_provider_rejects_remote_host() -> None:
    with pytest.raises(ValidationError):
        OllamaProvider(host="http://example.com:11434")


def test_provider_fallback_uses_ollama_even_with_cloud_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider = get_provider()

    assert isinstance(provider, OllamaProvider)
