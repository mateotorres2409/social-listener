from collections.abc import AsyncIterator

import httpx
import jsonschema
import pytest
from stubs.identity.app import create_app

from tests.unit.test_identity_contract import load

RESPONSE_SCHEMA = load()["components"]["schemas"]["LookupResponse"]


@pytest.fixture
async def stub() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(token="t", slow_seconds=0.01))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub") as client:
        yield client


async def _lookup(stub: httpx.AsyncClient, username: str, token: str = "t") -> httpx.Response:
    return await stub.post(
        "/v1/identity/lookups",
        json={"channel": "x", "external_author_id": "1", "username": username},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.parametrize(
    ("username", "status"),
    [
        ("cliente_ana", "match"),
        ("ambiguo_luis", "ambiguous"),
        ("lento_pedro", "no_match"),
        ("otro", "no_match"),
    ],
)
async def test_respuestas_cumplen_el_contrato(
    stub: httpx.AsyncClient, username: str, status: str
) -> None:
    response = await _lookup(stub, username)
    body = response.json()
    jsonschema.validate(body, RESPONSE_SCHEMA)
    assert body["status"] == status
    assert (body["customer_ref"] is not None) == (status == "match")


async def test_error_y_autenticacion(stub: httpx.AsyncClient) -> None:
    assert (await _lookup(stub, "error_x")).status_code == 503
    assert (await _lookup(stub, "cliente_ana", token="malo")).status_code == 401


async def test_solicitud_con_campos_extra_es_rechazada(stub: httpx.AsyncClient) -> None:
    response = await stub.post(
        "/v1/identity/lookups",
        json={"channel": "x", "external_author_id": "1", "username": "u", "texto": "no"},
        headers={"Authorization": "Bearer t"},
    )
    assert response.status_code == 422
