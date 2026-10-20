"""Dos workers concurrentes nunca toman el mismo trabajo (SKIP LOCKED)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from social_listener.application.dto.pipeline import Stage
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock
from tests.integration.test_postgres_persistence import truncate_all

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(migrated_url: str) -> AsyncIterator[SqlAlchemyUnitOfWorkFactory]:
    engine = create_engine(migrated_url, pool_size=4)
    factory = SqlAlchemyUnitOfWorkFactory(engine, FixedClock(T0), max_attempts=3)
    await truncate_all(factory)
    yield factory
    await engine.dispose()


async def test_workers_concurrentes_no_comparten_trabajos(
    factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    async with factory() as uow:
        for i in range(20):
            await uow.jobs.enqueue(Stage.CLASSIFY, f"m-{i}")
        await uow.commit()

    both_claimed = asyncio.Event()
    claimed: list[set[str]] = []

    async def worker() -> None:
        async with factory() as uow:
            jobs = await uow.jobs.claim(Stage.CLASSIFY, 15)
            claimed.append({j.subject_id for j in jobs})
            if len(claimed) == 2:
                both_claimed.set()
            # La transacción sigue abierta mientras el otro worker toma su lote.
            await asyncio.wait_for(both_claimed.wait(), timeout=10)
            for job in jobs:
                await uow.jobs.complete(job)
            await uow.commit()

    await asyncio.gather(worker(), worker())

    first, second = claimed
    assert first.isdisjoint(second)
    assert first | second == {f"m-{i}" for i in range(20)}
    async with factory() as uow:
        assert await uow.jobs.claim(Stage.CLASSIFY, 50) == []


async def test_trabajo_de_worker_caido_se_recupera_al_vencer_la_concesion(
    factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    clock = factory._clock
    assert isinstance(clock, FixedClock)
    async with factory() as uow:
        await uow.jobs.enqueue(Stage.EXPORT, "m-1")
        await uow.commit()
    async with factory() as uow:
        assert len(await uow.jobs.claim(Stage.EXPORT, 1)) == 1
        await uow.commit()  # el worker confirmó la toma y murió

    async with factory() as uow:
        assert await uow.jobs.claim(Stage.EXPORT, 1) == []
    clock._now = T0 + timedelta(minutes=6)
    async with factory() as uow:
        assert [j.subject_id for j in await uow.jobs.claim(Stage.EXPORT, 1)] == ["m-1"]
