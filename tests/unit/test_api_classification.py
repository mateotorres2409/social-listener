"""Corrección manual y taxonomía versionada desde la API."""

from __future__ import annotations

import pytest

from social_listener.application.dto.export import EventName
from social_listener.domain.classification.model import Dimension
from tests.contracts.persistence import P1, T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import ScriptedLLMClassifier
from tests.unit.api_support import AUTH, api_client, fake_dependencies
from tests.unit.test_classify_mention import classify, proposal, seeded_db


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
async def db(clock: FixedClock) -> InMemoryDatabase:
    return await seeded_db(clock)


async def _classified_mention(db: InMemoryDatabase) -> str:
    await classify(db, ScriptedLLMClassifier(responder=proposal()))
    async with db() as uow:
        (stored,) = uow.working.mentions.values()
    return stored.mention.id


async def test_correccion_manual(db: InMemoryDatabase, clock: FixedClock) -> None:
    mention_id = await _classified_mention(db)

    async with api_client(fake_dependencies(db, clock)) as api:
        response = await api.post(
            f"/v1/mentions/{mention_id}/classification/corrections",
            json={"corrections": {"tipo": "queja", "categoria": "tarjetas"}, "actor": "ana"},
            headers=AUTH,
        )
        mention = (await api.get(f"/v1/mentions/{mention_id}", headers=AUTH)).json()

    assert response.status_code == 200
    tipo = response.json()["dimensions"]["tipo"]
    assert (tipo["value"], tipo["source"], tipo["confidence"]) == ("queja", "manual", 1.0)
    assert mention["classification"]["dimensions"]["categoria"]["source"] == "manual"
    assert "[TARJETA]" in mention["masked_text"]

    state = db.state
    (event,) = state.outbox
    assert event.name is EventName.MENTION_UPDATED
    assert event.mention is not None
    assert event.mention.mention_id == mention_id
    assert event.mention.dimensions[Dimension.TIPO].value == "queja"
    assert state.audit[-1].actor == "ana"


async def test_correccion_con_valor_invalido(db: InMemoryDatabase, clock: FixedClock) -> None:
    mention_id = await _classified_mention(db)
    async with api_client(fake_dependencies(db, clock)) as api:
        response = await api.post(
            f"/v1/mentions/{mention_id}/classification/corrections",
            json={"corrections": {"tipo": "chisme"}},
            headers=AUTH,
        )
    assert response.status_code == 422
    assert db.state.outbox == []


async def test_nueva_taxonomia_no_altera_clasificaciones_previas(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    first = await _classified_mention(db)
    async with api_client(fake_dependencies(db, clock)) as api:
        default = (await api.get(f"/v1/projects/{P1}/taxonomy", headers=AUTH)).json()
        assert default["version"] == 1
        published = await api.put(
            f"/v1/projects/{P1}/taxonomy",
            json={
                "categories": [
                    {"code": "tarifas_costos", "name": "Tarifas"},
                    {"code": "nomina", "name": "Pagos de nómina"},
                ]
            },
            headers=AUTH,
        )
        assert published.json()["version"] == 2
        previous = (await api.get(f"/v1/mentions/{first}", headers=AUTH)).json()

    assert previous["classification"]["taxonomy_version"] == 1

    async with db() as uow:
        uow.working.mentions.clear()  # deja espacio para clasificar otra mención "m-1"
        uow.working.classifications.clear()
        await uow.commit()
    second = await classify(db, ScriptedLLMClassifier(responder=proposal()))
    assert second.taxonomy_version == 2
