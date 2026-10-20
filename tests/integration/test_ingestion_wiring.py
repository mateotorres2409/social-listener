"""La ingesta cableada por bootstrap funciona de punta a punta sobre PostgreSQL."""

from __future__ import annotations

import httpx
import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from social_listener.adapters.outbound.otel.telemetry import setup_telemetry
from social_listener.application.dto.pipeline import Stage
from social_listener.bootstrap.api import build_api
from social_listener.bootstrap.container import build_container
from social_listener.bootstrap.settings import load_settings
from social_listener.domain.mentions.model import Channel
from tests.integration.test_postgres_persistence import truncate_all
from tests.unit.test_settings import ENTORNO_VALIDO

pytestmark = pytest.mark.integration


async def test_api_e_ingesta_con_conector_mock(migrated_url: str) -> None:
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
    auth = {"Authorization": f"Bearer {settings.api_token.get_secret_value()}"}
    try:
        transport = httpx.ASGITransport(app=build_api(container))
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as api:
            project = (await api.post("/v1/projects", json={"name": "Banco"}, headers=auth)).json()
            query = (
                await api.post(
                    f"/v1/projects/{project['id']}/listening-queries",
                    json={"name": "Marca", "channels": ["mock"], "terms": ["BancoX"]},
                    headers=auth,
                )
            ).json()

        report = await container.ingest_mentions().run_cycle()
        assert report.created == 15
        again = await container.ingest_mentions().run_cycle()
        assert again.created == 0

        async with container.uow_factory() as uow:
            mention = await uow.mentions.find_by_external(Channel.MOCK, f"{query['id']}-0")
            assert mention is not None
            assert query["id"] in mention.query_ids
            assert len(await uow.jobs.claim(Stage.PROTECT, 100)) == 15
    finally:
        await container.aclose()
