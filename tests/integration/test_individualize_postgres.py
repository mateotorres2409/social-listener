"""Los escenarios de individualización también pasan sobre PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from social_listener.domain.mentions.model import Channel, ListeningQuery
from social_listener.domain.projects.model import OfficialAccount, Project, RecurrenceRule
from tests.contracts.persistence import P1, Q1, T0
from tests.fakes.memory import FixedClock
from tests.integration.test_postgres_persistence import truncate_all
from tests.unit.test_individualize import (  # noqa: F401 - se recolectan aquí con otro `db`
    test_actualizacion_del_perfil,
    test_autor_con_quejas_repetidas,
    test_cambio_de_username,
    test_la_entidad_responde_una_queja,
    test_primera_mencion_de_un_autor,
    test_respuesta_a_una_publicacion,
    test_respuesta_intermedia_que_llega_al_final,
    test_respuesta_que_llega_antes_que_el_padre,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0 + timedelta(days=1))


@pytest.fixture
async def db(migrated_url: str, clock: FixedClock) -> AsyncIterator[SqlAlchemyUnitOfWorkFactory]:
    engine = create_engine(migrated_url, pool_size=2)
    factory = SqlAlchemyUnitOfWorkFactory(engine, clock, max_attempts=5)
    await truncate_all(factory)
    async with factory() as uow:
        await uow.projects.save(
            Project(
                id=P1,
                name="Banco",
                recurrence=RecurrenceRule(threshold=2, window_days=30),
                official_accounts=frozenset({OfficialAccount(Channel.X, "banco-oficial")}),
            )
        )
        await uow.queries.add(
            ListeningQuery(id=Q1, project_id=P1, name="q", channels=("x",), terms=("b",))
        )
        await uow.commit()
    yield factory
    await engine.dispose()
