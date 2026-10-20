"""Instrumentación OpenTelemetry del runner: un span y métricas por etapa."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from opentelemetry import trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Status, StatusCode

from social_listener.application.dto.pipeline import Job, Stage


class OtelInstrumentation:
    def __init__(self, tracer: trace.Tracer, meter: Meter) -> None:
        self._tracer = tracer
        self._duration = meter.create_histogram(
            "social.pipeline.stage.duration",
            unit="s",
            description="Duración del procesamiento de un trabajo por etapa",
        )
        self._errors = meter.create_counter(
            "social.pipeline.stage.errors",
            description="Trabajos fallidos por etapa",
        )

    @asynccontextmanager
    async def job_span(self, job: Job) -> AsyncIterator[None]:
        with self._tracer.start_as_current_span(
            f"pipeline {job.stage.value}",
            kind=trace.SpanKind.CONSUMER,
            attributes={
                "social.pipeline.stage": job.stage.value,
                "social.pipeline.subject_id": job.subject_id,
                "social.pipeline.attempt": job.attempts + 1,
            },
        ) as span:
            try:
                yield
            except Exception as exc:
                # Solo el tipo: el mensaje de una excepción puede arrastrar datos personales.
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise

    def record_success(self, stage: Stage, seconds: float) -> None:
        self._duration.record(seconds, {"social.pipeline.stage": stage.value, "outcome": "ok"})

    def record_failure(self, stage: Stage, seconds: float, *, dead_letter: bool) -> None:
        attributes = {"social.pipeline.stage": stage.value, "outcome": "error"}
        self._duration.record(seconds, attributes)
        self._errors.add(1, {"social.pipeline.stage": stage.value, "dead_letter": dead_letter})
