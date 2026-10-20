"""Outbox en PostgreSQL + despachador OTLP contra un receptor OTLP real en memoria."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import timedelta

import pytest
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

from social_listener.adapters.outbound.otel.dispatcher import OutboxDispatcher
from social_listener.adapters.outbound.otel.telemetry import build_resource
from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from social_listener.application.backoff import Backoff
from social_listener.application.dto.export import EventName, IdentityResolved, OutboxEvent
from social_listener.domain.classification.model import Dimension
from social_listener.domain.identity.model import IdentityStatus
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock
from tests.fakes.otlp_receiver import OtlpReceiver, run_receiver
from tests.integration.test_postgres_persistence import truncate_all
from tests.unit.test_otel_schema_contract import dv, enriched

pytestmark = pytest.mark.integration

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
SPAN_ID = "00f067aa0ba902b7"


@pytest.fixture
def receiver() -> Iterator[OtlpReceiver]:
    yield from run_receiver()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
async def setup(
    migrated_url: str, clock: FixedClock
) -> AsyncIterator[tuple[SqlAlchemyUnitOfWorkFactory, object]]:
    engine = create_engine(migrated_url, pool_size=2)
    factory = SqlAlchemyUnitOfWorkFactory(
        engine, clock, max_attempts=5, trace_context=lambda: (TRACE_ID, SPAN_ID)
    )
    await truncate_all(factory)
    yield factory, engine
    await engine.dispose()


def dispatcher(engine: object, receiver: OtlpReceiver, clock: FixedClock) -> OutboxDispatcher:
    exporter = OTLPLogExporter(endpoint=f"{receiver.endpoint}/v1/logs", timeout=1)
    return OutboxDispatcher(
        engine,  # type: ignore[arg-type]
        exporter,
        build_resource("0.1.0", "test"),
        clock,
        backoff=Backoff(base_s=10, jitter=0),
    )


async def add(factory: SqlAlchemyUnitOfWorkFactory, *events: OutboxEvent) -> None:
    async with factory() as uow:
        for event in events:
            await uow.outbox.add(event)
        await uow.commit()


def critical() -> OutboxEvent:
    m = enriched()
    dims = {**m.dimensions, Dimension.IMPACTO: dv("critico")}
    return OutboxEvent(EventName.MENTION_ENRICHED, T0, mention=replace(m, dimensions=dims))


async def test_mencion_critica_llega_con_severidad_error_y_traza(
    setup: tuple[SqlAlchemyUnitOfWorkFactory, object], receiver: OtlpReceiver, clock: FixedClock
) -> None:
    factory, engine = setup
    await add(factory, critical())

    assert await dispatcher(engine, receiver, clock).run_once() == 1

    (log,) = receiver.logs
    assert log.event_name == "social.mention.enriched"
    assert log.severity_text == "ERROR"
    assert log.body == "me cobraron en la tarjeta [TARJETA]"
    assert log.attributes["social.mention.id"] == "m-1"
    assert log.attributes["social.schema.version"] == "1.0.0"
    assert (log.trace_id, log.span_id) == (TRACE_ID, SPAN_ID)
    assert log.resource["service.name"] == "social-listener"


async def test_collector_caido_no_pierde_registros(
    setup: tuple[SqlAlchemyUnitOfWorkFactory, object], receiver: OtlpReceiver, clock: FixedClock
) -> None:
    factory, engine = setup
    await add(factory, critical())
    receiver.available = False
    sender = dispatcher(engine, receiver, clock)

    assert await sender.run_once() == 0
    assert receiver.rejected_requests >= 1
    assert receiver.logs == []
    assert await sender.run_once() == 0  # aún no vence el backoff: ni siquiera lo intenta

    receiver.available = True
    clock.advance(seconds=11)
    assert await sender.run_once() == 1
    assert len(receiver.logs) == 1
    assert await sender.run_once() == 0  # ya confirmado: no se reenvía


async def test_reinicio_con_registros_pendientes(
    setup: tuple[SqlAlchemyUnitOfWorkFactory, object], receiver: OtlpReceiver, clock: FixedClock
) -> None:
    factory, engine = setup
    await add(factory, critical(), critical())
    receiver.available = False
    await dispatcher(engine, receiver, clock).run_once()  # el proceso "muere" aquí

    receiver.available = True
    clock.advance(seconds=11)
    restarted = dispatcher(engine, receiver, clock)
    assert await restarted.run_once() == 2
    assert len(receiver.logs) == 2


async def test_eventos_de_actualizacion(
    setup: tuple[SqlAlchemyUnitOfWorkFactory, object], receiver: OtlpReceiver, clock: FixedClock
) -> None:
    factory, engine = setup
    updated = OutboxEvent(
        EventName.MENTION_UPDATED,
        T0 + timedelta(minutes=1),
        mention=enriched(),
        attributes={
            "social.update.reason": "correccion_manual",
            "social.update.dimensions": ["tipo"],
        },
    )
    resolved = OutboxEvent(
        EventName.AUTHOR_IDENTITY_RESOLVED,
        T0 + timedelta(minutes=2),
        identity=IdentityResolved("k1:abc", "p-1", IdentityStatus.IDENTIFICADO, "k1:def"),
    )
    await add(factory, updated, resolved)

    assert await dispatcher(engine, receiver, clock).run_once() == 2

    by_name = {log.event_name: log for log in receiver.logs}
    assert by_name["social.mention.updated"].attributes["social.mention.id"] == "m-1"
    assert by_name["social.mention.updated"].attributes["social.update.dimensions"] == ["tipo"]
    identity = by_name["social.author.identity.resolved"]
    assert identity.attributes["social.author.pseudonym_id"] == "k1:abc"
    assert identity.attributes["social.identity.customer_pseudonym"] == "k1:def"
