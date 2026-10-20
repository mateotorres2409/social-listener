"""El adaptador PostgreSQL cumple la misma batería de contratos que el fake."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from social_listener.adapters.outbound.persistence.models import Base
from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from tests.contracts.persistence import T0, PersistenceContract
from tests.fakes.memory import FixedClock

pytestmark = pytest.mark.integration


async def truncate_all(factory: SqlAlchemyUnitOfWorkFactory) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with factory() as uow:
        await uow.session.execute(text(f"TRUNCATE {tables} CASCADE"))
        await uow.commit()


class TestPostgresPersistence(PersistenceContract):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock(T0)

    @pytest.fixture
    async def uow_factory(
        self, migrated_url: str, clock: FixedClock
    ) -> AsyncIterator[SqlAlchemyUnitOfWorkFactory]:
        engine = create_engine(migrated_url, pool_size=2)
        factory = SqlAlchemyUnitOfWorkFactory(engine, clock, max_attempts=5)
        await truncate_all(factory)
        yield factory
        await engine.dispose()
