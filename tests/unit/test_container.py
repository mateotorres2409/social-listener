from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from social_listener.adapters.inbound.worker.instrumentation import OtelInstrumentation
from social_listener.adapters.outbound.otel.telemetry import setup_telemetry
from social_listener.adapters.outbound.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from social_listener.bootstrap.container import build_container
from social_listener.bootstrap.settings import load_settings
from tests.unit.test_settings import ENTORNO_VALIDO


async def test_contenedor_cablea_persistencia_e_instrumentacion() -> None:
    telemetry = setup_telemetry(
        service_version="0.1.0",
        environment="test",
        span_exporter=InMemorySpanExporter(),
        metric_reader=InMemoryMetricReader(),
        set_global=False,
    )
    container = build_container(load_settings(ENTORNO_VALIDO), telemetry=telemetry)
    try:
        assert isinstance(container.uow_factory, SqlAlchemyUnitOfWorkFactory)
        assert isinstance(container.instrumentation, OtelInstrumentation)
        assert container.telemetry.resource.attributes["service.name"] == "social-listener"
    finally:
        await container.aclose()
