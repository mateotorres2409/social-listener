"""Configuración del SDK de OpenTelemetry (trazas y métricas del pipeline).

El destino se configura con las variables estándar ``OTEL_EXPORTER_OTLP_*``
(endpoint, protocolo, cabeceras, certificados) y ``OTEL_RESOURCE_ATTRIBUTES``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.sdk._logs.export import LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

SERVICE_NAME = "social-listener"
GRPC = "grpc"
HTTP_PROTOBUF = "http/protobuf"


def build_resource(service_version: str, environment: str) -> Resource:
    """``Resource.create`` también incorpora ``OTEL_RESOURCE_ATTRIBUTES``."""
    return Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": service_version,
            "deployment.environment.name": environment,
        }
    )


def otlp_protocol(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return env.get("OTEL_EXPORTER_OTLP_PROTOCOL", HTTP_PROTOBUF).strip().lower()


def otlp_span_exporter() -> SpanExporter:
    if otlp_protocol() == GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter()
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpSpanExporter,
    )

    return HttpSpanExporter()


def otlp_metric_reader(interval_ms: int = 15_000) -> MetricReader:
    if otlp_protocol() == GRPC:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        return PeriodicExportingMetricReader(OTLPMetricExporter(), interval_ms)
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as HttpMetricExporter,
    )

    return PeriodicExportingMetricReader(HttpMetricExporter(), interval_ms)


@dataclass
class Telemetry:
    resource: Resource
    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def tracer(self, name: str = SERVICE_NAME) -> trace.Tracer:
        return self.tracer_provider.get_tracer(name)

    def force_flush(self) -> None:
        self.tracer_provider.force_flush()
        self.meter_provider.force_flush()

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def setup_telemetry(
    *,
    service_version: str,
    environment: str,
    span_exporter: SpanExporter | None = None,
    metric_reader: MetricReader | None = None,
    set_global: bool = True,
) -> Telemetry:
    resource = build_resource(service_version, environment)
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter or otlp_span_exporter()))
    meter_provider = MeterProvider(
        resource=resource, metric_readers=[metric_reader or otlp_metric_reader()]
    )
    if set_global:
        trace.set_tracer_provider(tracer_provider)
    return Telemetry(resource, tracer_provider, meter_provider)


def current_trace_context() -> tuple[str | None, str | None]:
    """trace_id y span_id del span activo, en hexadecimal, para el outbox."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def otlp_log_exporter(timeout_s: float = 10.0) -> LogRecordExporter:
    """Exportador de logs OTLP configurado por ``OTEL_EXPORTER_OTLP_*``."""
    if otlp_protocol() == GRPC:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

        return OTLPLogExporter(timeout=timeout_s)
    from opentelemetry.exporter.otlp.proto.http._log_exporter import (
        OTLPLogExporter as HttpLogExporter,
    )

    return HttpLogExporter(timeout=timeout_s)
