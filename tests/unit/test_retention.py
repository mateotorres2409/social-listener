"""Escenarios de retención y supresión de ``proteccion-datos``."""

from __future__ import annotations

from datetime import timedelta

import pytest

from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.application.use_cases.privacy.retention import (
    ApplyRetention,
    SuppressAuthorData,
    author_pseudonym,
)
from social_listener.domain.identity.model import IdentityResult, IdentityStatus
from social_listener.domain.mentions.model import Channel, ListeningQuery
from social_listener.domain.projects.model import Project
from tests.contracts.persistence import P1, Q1, T0, author, incoming
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import HmacPseudonymizer
from tests.unit.api_support import AUTH, api_client, fake_dependencies


async def seed(factory: UnitOfWorkFactory) -> dict[str, str]:
    """Un autor con una mención vencida (400 días) y otra reciente, más su identidad."""
    a = author(last_seen_at=T0, first_seen_at=T0 - timedelta(days=400))
    async with factory() as uow:
        await uow.projects.save(Project(id=P1, name="Banco", retention_days=365))
        await uow.queries.add(
            ListeningQuery(id=Q1, project_id=P1, name="q", channels=("x",), terms=("b",))
        )
        await uow.authors.save(a)
        old, _ = await uow.mentions.add_or_get(
            P1, incoming("viejo", published_at=T0 - timedelta(days=400)), Q1
        )
        new, _ = await uow.mentions.add_or_get(P1, incoming("nuevo"), Q1)
        await uow.mentions.assign_author(old.id, a.id, None)
        await uow.mentions.assign_author(new.id, a.id, None)
        await uow.identity_results.save(
            IdentityResult(
                author_id=a.id,
                status=IdentityStatus.IDENTIFICADO,
                validated_at=T0,
                username=a.username,
                customer_ref="ref-1",
            )
        )
        await uow.commit()
    return {"author": a.id, "old": old.id, "new": new.id}


class RetentionScenarios:
    async def test_mencion_vencida(self, factory: UnitOfWorkFactory, clock: FixedClock) -> None:
        ids = await seed(factory)

        report = await ApplyRetention(factory, clock).execute()

        (project,) = report.projects
        assert project.mentions_purged == 1
        assert project.authors_deleted == 0  # el autor sigue activo
        async with factory() as uow:
            old = await uow.mentions.get(ids["old"])  # type: ignore[arg-type]
            assert old is not None
            assert await uow.mentions.get_raw_text(old.id) is None
            assert old.data.author.username == "suprimido"
            assert await uow.mentions.get_raw_text(ids["new"]) is not None  # type: ignore[arg-type]
            (entry,) = await uow.audit.list_recent(5)
            assert entry.subject == f"proyecto:{P1}"

    async def test_autor_inactivo_se_elimina(
        self, factory: UnitOfWorkFactory, clock: FixedClock
    ) -> None:
        ids = await seed(factory)
        clock.advance(days=400)

        report = await ApplyRetention(factory, clock).execute()

        assert report.projects[0].authors_deleted == 1
        async with factory() as uow:
            assert await uow.authors.get(ids["author"]) is None  # type: ignore[arg-type]
            assert await uow.identity_results.get(ids["author"]) is None  # type: ignore[arg-type]

    async def test_solicitud_de_supresion(
        self, factory: UnitOfWorkFactory, clock: FixedClock
    ) -> None:
        ids = await seed(factory)
        pseudonymizer = HmacPseudonymizer()

        result = await SuppressAuthorData(factory, pseudonymizer, clock).execute(
            P1, Channel.X, "a-1", actor="oficial-de-privacidad"
        )

        assert result.found
        assert result.mentions_anonymized == 2
        async with factory() as uow:
            assert await uow.authors.get_by_external(P1, Channel.X, "a-1") is None
            assert await uow.identity_results.get(ids["author"]) is None  # type: ignore[arg-type]
            for key in ("old", "new"):
                assert await uow.mentions.get_raw_text(ids[key]) is None  # type: ignore[arg-type]
            (entry,) = await uow.audit.list_recent(5)
            assert entry.action == "supresion"
            assert entry.subject == author_pseudonym(pseudonymizer, Channel.X, "a-1")
            assert "a-1" not in entry.subject
            assert "juanp" not in entry.subject

        # Una mención futura del mismo autor se procesa como de un autor nuevo.
        async with factory() as uow:
            mention, created = await uow.mentions.add_or_get(P1, incoming("futura"), Q1)
            assert created
            assert await uow.mentions.author_of(mention.id) is None

    async def test_supresion_de_autor_desconocido_queda_auditada(
        self, factory: UnitOfWorkFactory, clock: FixedClock
    ) -> None:
        await seed(factory)
        result = await SuppressAuthorData(factory, HmacPseudonymizer(), clock).execute(
            P1, Channel.X, "otro", actor="api"
        )
        assert not result.found
        async with factory() as uow:
            (entry,) = await uow.audit.list_recent(5)
            assert entry.action == "supresion_sin_datos"


class TestRetentionInMemory(RetentionScenarios):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock(T0)

    @pytest.fixture
    def factory(self, clock: FixedClock) -> InMemoryDatabase:
        return InMemoryDatabase(clock)


async def test_endpoint_de_supresion() -> None:
    clock = FixedClock(T0)
    db = InMemoryDatabase(clock)
    await seed(db)
    async with api_client(fake_dependencies(db, clock)) as api:
        response = await api.post(
            f"/v1/projects/{P1}/authors/suppress",
            json={"channel": "x", "external_id": "a-1", "requested_by": "dpo"},
            headers=AUTH,
        )
    assert response.json() == {"found": True, "mentions_anonymized": 2}
