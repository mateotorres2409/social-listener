"""El cliente HTTP de identidad contra el stub (ASGI) y contra transportes falsos."""

from __future__ import annotations

import json

import httpx
import pytest
from stubs.identity.app import create_app

from social_listener.adapters.outbound.identity.http_client import (
    ClientCredentials,
    HttpIdentityService,
)
from social_listener.adapters.outbound.resilience import CircuitBreaker
from social_listener.application.dto.identity import IdentityLookup
from social_listener.application.errors import IdentityServiceUnavailableError
from tests.contracts.services import IdentityServiceContract


def stub_service(**kw: object) -> HttpIdentityService:
    transport = httpx.ASGITransport(app=create_app(token="", slow_seconds=0.01))
    return HttpIdentityService(
        "http://stub",
        client=httpx.AsyncClient(transport=transport),
        **kw,  # type: ignore[arg-type]
    )


def failing_transport(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("sin ruta")


class TestHttpIdentityContract(IdentityServiceContract):
    @pytest.fixture
    def identity_service(self) -> HttpIdentityService:
        return stub_service()

    @pytest.fixture
    def unavailable_identity_service(self) -> HttpIdentityService:
        client = httpx.AsyncClient(transport=httpx.MockTransport(failing_transport))
        return HttpIdentityService("http://caido", client=client)


async def test_la_solicitud_solo_lleva_canal_id_y_username() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"status": "match", "customer_ref": "c-1", "confidence": 0.9, "nombre": "Ana"},
        )

    service = HttpIdentityService(
        "http://id", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    result = await service.lookup(IdentityLookup("x", "123", "ana"))

    assert seen == [{"channel": "x", "external_author_id": "123", "username": "ana"}]
    assert not hasattr(result, "nombre")
    assert set(result.__dataclass_fields__) == {"status", "customer_ref", "confidence"}


async def test_circuito_abierto_no_llama_al_servicio() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    service = HttpIdentityService(
        "http://id",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        breaker=CircuitBreaker(failure_threshold=2, cooldown_s=60),
    )
    for _ in range(4):
        with pytest.raises(IdentityServiceUnavailableError):
            await service.lookup(IdentityLookup("x", "1", "u"))
    assert calls == 2


async def test_timeout_del_servicio() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    service = HttpIdentityService(
        "http://id", client=httpx.AsyncClient(transport=httpx.MockTransport(slow)), timeout_s=0.05
    )
    with pytest.raises(IdentityServiceUnavailableError, match="ReadTimeout"):
        await service.lookup(IdentityLookup("x", "1", "lento_u"))


async def test_respuesta_fuera_del_contrato() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "quizas"})

    service = HttpIdentityService(
        "http://id", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(IdentityServiceUnavailableError, match="contrato"):
        await service.lookup(IdentityLookup("x", "1", "u"))


async def test_oauth2_client_credentials_con_cache_del_token() -> None:
    token_calls = 0
    auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth/token":
            token_calls += 1
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        auth_headers.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"status": "no_match", "customer_ref": None})

    service = HttpIdentityService(
        "http://id",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        credentials=ClientCredentials("http://id/oauth/token", "sl", "secreto"),
    )
    for _ in range(3):
        await service.lookup(IdentityLookup("x", "1", "u"))

    assert token_calls == 1
    assert auth_headers == ["Bearer tok-1"] * 3
