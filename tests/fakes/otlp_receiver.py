"""Receptor OTLP/HTTP en memoria para pruebas.

Decodifica las solicitudes protobuf reales que envían los exportadores OTLP y las
guarda para afirmar sobre ellas. ``available = False`` simula un Collector caído.
"""

from __future__ import annotations

import gzip
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue


def any_value(value: AnyValue) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    raw = getattr(value, kind)
    if kind == "array_value":
        return [any_value(v) for v in raw.values]
    if kind == "kvlist_value":
        return {kv.key: any_value(kv.value) for kv in raw.values}
    return raw


def attributes(kvs: Any) -> dict[str, Any]:
    return {kv.key: any_value(kv.value) for kv in kvs if isinstance(kv, KeyValue)}


@dataclass
class ReceivedLog:
    resource: dict[str, Any]
    event_name: str
    severity_text: str
    severity_number: int
    body: Any
    attributes: dict[str, Any]
    trace_id: str
    span_id: str


@dataclass
class ReceivedSpan:
    resource: dict[str, Any]
    name: str
    attributes: dict[str, Any]
    trace_id: str
    span_id: str


@dataclass
class OtlpReceiver:
    available: bool = True
    logs: list[ReceivedLog] = field(default_factory=list)
    spans: list[ReceivedSpan] = field(default_factory=list)
    metrics: list[ExportMetricsServiceRequest] = field(default_factory=list)
    rejected_requests: int = 0
    port: int = 0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def metric_points(self, name: str) -> list[tuple[dict[str, Any], float]]:
        points: list[tuple[dict[str, Any], float]] = []
        for request in self.metrics:
            for rm in request.resource_metrics:
                for sm in rm.scope_metrics:
                    for metric in sm.metrics:
                        if metric.name != name:
                            continue
                        data = getattr(metric, metric.WhichOneof("data"))
                        for point in data.data_points:
                            value = (
                                point.count
                                if hasattr(point, "bucket_counts")
                                else (point.as_int or point.as_double)
                            )
                            points.append((attributes(point.attributes), float(value)))
        return points

    def _handle(self, path: str, payload: bytes) -> None:
        if path == "/v1/logs":
            logs_request = ExportLogsServiceRequest.FromString(payload)
            for rl in logs_request.resource_logs:
                resource = attributes(rl.resource.attributes)
                for sl in rl.scope_logs:
                    for record in sl.log_records:
                        self.logs.append(
                            ReceivedLog(
                                resource=resource,
                                event_name=record.event_name,
                                severity_text=record.severity_text,
                                severity_number=record.severity_number,
                                body=any_value(record.body),
                                attributes=attributes(record.attributes),
                                trace_id=record.trace_id.hex(),
                                span_id=record.span_id.hex(),
                            )
                        )
        elif path == "/v1/traces":
            trace_request = ExportTraceServiceRequest.FromString(payload)
            for rs in trace_request.resource_spans:
                resource = attributes(rs.resource.attributes)
                for ss in rs.scope_spans:
                    for span in ss.spans:
                        self.spans.append(
                            ReceivedSpan(
                                resource=resource,
                                name=span.name,
                                attributes=attributes(span.attributes),
                                trace_id=span.trace_id.hex(),
                                span_id=span.span_id.hex(),
                            )
                        )
        elif path == "/v1/metrics":
            self.metrics.append(ExportMetricsServiceRequest.FromString(payload))


def _handler_for(receiver: OtlpReceiver) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            if not receiver.available:
                receiver.rejected_requests += 1
                self.send_response(503)
                self.end_headers()
                return
            receiver._handle(self.path, body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return None

    return Handler


def run_receiver() -> Iterator[OtlpReceiver]:
    receiver = OtlpReceiver()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(receiver))
    receiver.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield receiver
    finally:
        server.shutdown()
        server.server_close()
