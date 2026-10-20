from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from social_listener.adapters.inbound.worker.instrumentation import OtelInstrumentation
from social_listener.adapters.inbound.worker.runner import StageRunner
from social_listener.adapters.outbound.otel.telemetry import (
    SERVICE_NAME,
    current_trace_context,
    otlp_metric_reader,
    otlp_span_exporter,
    setup_telemetry,
)
from social_listener.application.backoff import Backoff
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.ports.inbound.stages import ADVANCE, StageOutcome
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.otlp_receiver import OtlpReceiver, run_receiver


@pytest.fixture
def receiver(monkeypatch: pytest.MonkeyPatch) -> Iterator[OtlpReceiver]:
    for r in run_receiver():
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", r.endpoint)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.namespace=banca")
        yield r


@dataclass
class TraceCapturingHandler:
    fail: bool = False
    trace_ids: tuple[str | None, str | None] = (None, None)

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        self.trace_ids = current_trace_context()
        if self.fail:
            raise ValueError("dato personal que no debe salir: juanp")
        return ADVANCE


async def test_span_de_etapa_llega_por_otlp_con_atributos_de_recurso(
    receiver: OtlpReceiver,
) -> None:
    telemetry = setup_telemetry(
        service_version="0.1.0",
        environment="test",
        span_exporter=otlp_span_exporter(),
        metric_reader=otlp_metric_reader(interval_ms=60_000),
        set_global=False,
    )
    instrumentation = OtelInstrumentation(
        telemetry.tracer(), telemetry.meter_provider.get_meter(SERVICE_NAME)
    )
    clock = FixedClock(T0)
    db = InMemoryDatabase(clock)
    async with db() as uow:
        await uow.jobs.enqueue(Stage.CLASSIFY, "m-1")
        await uow.jobs.enqueue(Stage.PROTECT, "m-2")
        await uow.commit()
    handler = TraceCapturingHandler()
    await StageRunner(
        Stage.CLASSIFY, handler, db, clock, instrumentation=instrumentation
    ).run_once()
    failing = TraceCapturingHandler(fail=True)
    await StageRunner(
        Stage.PROTECT, failing, db, clock, instrumentation=instrumentation, backoff=Backoff()
    ).run_once()

    telemetry.force_flush()
    telemetry.shutdown()

    spans = {s.name: s for s in receiver.spans}
    ok = spans["pipeline clasificacion"]
    assert ok.resource["service.name"] == "social-listener"
    assert ok.resource["service.version"] == "0.1.0"
    assert ok.resource["deployment.environment.name"] == "test"
    assert ok.resource["service.namespace"] == "banca"
    assert ok.attributes["social.pipeline.stage"] == "clasificacion"
    assert ok.attributes["social.pipeline.subject_id"] == "m-1"
    assert (ok.trace_id, ok.span_id) == handler.trace_ids

    failed = spans["pipeline proteccion"]
    assert failed.attributes["error.type"] == "ValueError"
    assert "juanp" not in str(failed.attributes)

    durations = receiver.metric_points("social.pipeline.stage.duration")
    assert {a["outcome"] for a, _ in durations} == {"ok", "error"}
    # Métricas acumulativas: cada exportación (flush y shutdown) repite el total.
    errors = receiver.metric_points("social.pipeline.stage.errors")
    assert errors[-1] == ({"social.pipeline.stage": "proteccion", "dead_letter": False}, 1.0)


def test_contexto_de_traza_vacio_fuera_de_un_span() -> None:
    assert current_trace_context() == (None, None)
