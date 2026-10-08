from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    REQUEST_ID_HEADER,
    ValidationError,
    add_request_id_middleware,
    register_exception_handlers,
)


def _app() -> FastAPI:
    app = FastAPI()
    add_request_id_middleware(app)
    register_exception_handlers(app)

    @app.get("/validation-error")
    async def validation_error() -> None:
        raise ValidationError("Bad input", details={"field": "name"})

    return app


def test_error_response_includes_generated_request_id() -> None:
    response = TestClient(_app()).get("/validation-error")

    body = response.json()
    request_id = response.headers[REQUEST_ID_HEADER]
    assert response.status_code == 422
    assert request_id
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] == request_id
    assert body["error"]["details"] == {"field": "name"}


def test_error_response_preserves_inbound_request_id() -> None:
    response = TestClient(_app()).get(
        "/validation-error",
        headers={REQUEST_ID_HEADER: "test-request-123"},
    )

    assert response.headers[REQUEST_ID_HEADER] == "test-request-123"
    assert response.json()["error"]["request_id"] == "test-request-123"
