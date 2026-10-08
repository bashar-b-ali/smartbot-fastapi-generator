from __future__ import annotations

from app.services import ollama_runtime
from app.services.ollama_runtime import _is_model_installed


def test_ollama_model_installed_matches_latest_alias() -> None:
    assert _is_model_installed("llama3.2", {"llama3.2:latest"})
    assert _is_model_installed("fastAPI_Model", {"fastapi_model:latest"})


class FakeClient:
    def __init__(self) -> None:
        self.pulled = False
        self.generated = False

    def list(self):
        return {"models": []}

    def pull(self, **kwargs):  # pragma: no cover - must not be called
        self.pulled = True
        raise AssertionError("pull should not be called")

    def generate(self, **kwargs):
        self.generated = True


class FakeProvider:
    last_client: FakeClient | None = None
    DEFAULT_MODEL = "fastAPI_Model"

    def __init__(self, *args, **kwargs) -> None:
        self._client = FakeClient()
        FakeProvider.last_client = self._client


def test_ollama_warmup_never_pulls_missing_models(monkeypatch) -> None:
    monkeypatch.setattr(ollama_runtime, "OllamaProvider", FakeProvider)
    monkeypatch.setattr(ollama_runtime.settings, "llm_model", "fastAPI_Model")

    ollama_runtime._warm_server_model_sync()

    assert FakeProvider.last_client is not None
    assert FakeProvider.last_client.pulled is False
    assert FakeProvider.last_client.generated is False
