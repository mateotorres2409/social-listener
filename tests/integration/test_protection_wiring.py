"""La etapa de protección cableada por bootstrap enmascara sobre PostgreSQL."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from social_listener.adapters.inbound.worker.runner import StageRunner
from social_listener.adapters.outbound.otel.telemetry import setup_telemetry
from social_listener.application.dto.pipeline import Stage
from social_listener.bootstrap.cli import STAGE_HANDLERS
from social_listener.bootstrap.container import build_container
from social_listener.bootstrap.settings import load_settings
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.projects.model import Project
from tests.contracts.persistence import P1, Q1, incoming
from tests.integration.test_postgres_persistence import truncate_all
from tests.unit.test_settings import ENTORNO_VALIDO

pytestmark = pytest.mark.integration


async def test_worker_de_proteccion(migrated_url: str) -> None:
    settings = load_settings({**ENTORNO_VALIDO, "SL_DATABASE_URL": migrated_url})
    telemetry = setup_telemetry(
        service_version="0.1.0",
        environment="test",
        span_exporter=InMemorySpanExporter(),
        metric_reader=InMemoryMetricReader(),
        set_global=False,
    )
    container = build_container(settings, telemetry=telemetry)
    await truncate_all(container.uow_factory)  # type: ignore[arg-type]
    try:
        base = incoming("m-1")
        async with container.uow_factory() as uow:
            await uow.projects.save(Project(id=P1, name="Banco"))
            await uow.queries.add(
                ListeningQuery(id=Q1, project_id=P1, name="q", channels=("x",), terms=("b",))
            )
            mention, _ = await uow.mentions.add_or_get(
                P1, type(base)(data=base.data, raw_text="mi cédula es 1.020.345.678"), Q1
            )
            await uow.jobs.enqueue(Stage.PROTECT, mention.id)
            await uow.commit()

        handler = STAGE_HANDLERS[Stage.PROTECT](container)
        runner = StageRunner(Stage.PROTECT, handler, container.uow_factory, container.clock)
        assert await runner.run_once()

        async with container.uow_factory() as uow:
            stored = await uow.mentions.get(mention.id)
            assert stored is not None
            assert stored.text is not None
            assert stored.text.value == "mi cédula es [DOCUMENTO]"
            assert len(await uow.jobs.claim(Stage.CLASSIFY, 5)) == 1
    finally:
        await container.aclose()
