"""API de administración: proyectos, tópicos de escucha y cola de errores."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from social_listener.application.dto.pipeline import Stage
from social_listener.application.use_cases.ingestion.ingest import (
    IngestMentions,
)
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import ScriptedConnector
from tests.unit.api_support import AUTH, api_client, fake_dependencies
from tests.unit.test_ingestion import raw


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def db(clock: FixedClock) -> InMemoryDatabase:
    return InMemoryDatabase(clock)


@pytest.fixture
async def client(db: InMemoryDatabase, clock: FixedClock) -> AsyncIterator[httpx.AsyncClient]:
    async with api_client(fake_dependencies(db, clock)) as c:
        yield c


async def _project(client: httpx.AsyncClient) -> str:
    response = await client.post("/v1/projects", json={"name": "Banco"}, headers=AUTH)
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_sin_token_responde_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/projects")).status_code == 401
    bad = {"Authorization": "Bearer otro"}
    assert (await client.get("/v1/projects", headers=bad)).status_code == 401
    assert (await client.get("/health")).status_code == 200


async def test_crud_de_topicos(client: httpx.AsyncClient) -> None:
    project_id = await _project(client)
    body = {"name": "Marca", "channels": ["x"], "terms": ["banco"]}

    created = await client.post(
        f"/v1/projects/{project_id}/listening-queries", json=body, headers=AUTH
    )
    assert created.status_code == 201
    query = created.json()
    assert query["status"] == "activo"

    updated = await client.put(
        f"/v1/listening-queries/{query['id']}",
        json={**body, "hashtags": ["#banco"]},
        headers=AUTH,
    )
    assert updated.json()["hashtags"] == ["#banco"]
    listed = await client.get(f"/v1/projects/{project_id}/listening-queries", headers=AUTH)
    assert [q["id"] for q in listed.json()] == [query["id"]]

    deleted = await client.delete(f"/v1/listening-queries/{query['id']}", headers=AUTH)
    assert deleted.status_code == 204
    missing = await client.get(f"/v1/listening-queries/{query['id']}", headers=AUTH)
    assert missing.status_code == 404


async def test_topico_sin_criterios_es_rechazado(client: httpx.AsyncClient) -> None:
    project_id = await _project(client)
    response = await client.post(
        f"/v1/projects/{project_id}/listening-queries",
        json={"name": "Marca", "channels": ["x"]},
        headers=AUTH,
    )
    assert response.status_code == 422
    listed = await client.get(f"/v1/projects/{project_id}/listening-queries", headers=AUTH)
    assert listed.json() == []


async def test_topico_pausado_desde_la_api_no_captura(
    client: httpx.AsyncClient, db: InMemoryDatabase, clock: FixedClock
) -> None:
    project_id = await _project(client)
    query = (
        await client.post(
            f"/v1/projects/{project_id}/listening-queries",
            json={"name": "Marca", "channels": ["x"], "terms": ["banco"]},
            headers=AUTH,
        )
    ).json()
    paused = await client.post(f"/v1/listening-queries/{query['id']}/pause", headers=AUTH)
    assert paused.json()["status"] == "pausado"

    connector = ScriptedConnector([[raw()]], name="x")
    await IngestMentions(db, {"x": connector}, clock).run_cycle()
    assert connector.calls == []

    await client.post(f"/v1/listening-queries/{query['id']}/resume", headers=AUTH)
    await IngestMentions(db, {"x": connector}, clock).run_cycle()
    assert connector.calls == [None]


async def test_reprocesar_desde_la_etapa_fallida(
    client: httpx.AsyncClient, db: InMemoryDatabase
) -> None:
    async with db() as uow:
        await uow.jobs.enqueue(Stage.IDENTITY, "m-7")
        (job,) = await uow.jobs.claim(Stage.IDENTITY, 1)
        await uow.jobs.fail(job, "definitivo", retry_at=None)
        await uow.commit()

    (letter,) = (await client.get("/v1/dead-letters", headers=AUTH)).json()
    assert letter["stage"] == "identidad"

    result = await client.post(f"/v1/dead-letters/{letter['id']}/reprocess", headers=AUTH)

    assert result.json() == {"stage": "identidad", "accepted": True, "reason": None}
    async with db() as uow:
        assert [j.subject_id for j in uow.jobs.pending(Stage.IDENTITY)] == ["m-7"]
    assert (await client.get("/v1/dead-letters", headers=AUTH)).json() == []
