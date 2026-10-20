"""Retención y supresión sobre PostgreSQL (mismos escenarios que en memoria)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock
from tests.integration.test_postgres_persistence import truncate_all
from tests.unit.test_retention import RetentionScenarios

pytestmark = pytest.mark.integration


class TestRetentionPostgres(RetentionScenarios):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock(T0)

    @pytest.fixture
    async def factory(
        self, migrated_url: str, clock: FixedClock
    ) -> AsyncIterator[SqlAlchemyUnitOfWorkFactory]:
        engine = create_engine(migrated_url, pool_size=2)
        factory = SqlAlchemyUnitOfWorkFactory(engine, clock, max_attempts=5)
        await truncate_all(factory)
        yield factory
        await engine.dispose()
