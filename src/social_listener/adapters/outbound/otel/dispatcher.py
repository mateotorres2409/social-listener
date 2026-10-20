"""Despachador del outbox: envía los eventos por OTLP con entrega al menos una vez.

No usa ``BatchLogRecordProcessor`` porque descarta registros si el Collector no
responde. Aquí un registro solo se marca como enviado cuando ``export`` devuelve
``SUCCESS``; si no, se reprograma con backoff y sobrevive a reinicios.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from opentelemetry import trace
from opentelemetry._logs import LogRecord
from opentelemetry.sdk._logs import ReadableLogRecord
from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from social_listener import __version__
from social_listener.adapters.outbound.otel.mapping import to_log
from social_listener.adapters.outbound.persistence.codecs import outbox_event_from_json
from social_listener.adapters.outbound.persistence.models import OutboxRow
from social_listener.application.backoff import Backoff
from social_listener.application.ports.outbound.services import Clock

logger = logging.getLogger(__name__)

SCOPE = InstrumentationScope("social_listener.export", __version__)


class OutboxDispatcher:
    def __init__(
        self,
        engine: AsyncEngine,
        exporter: LogRecordExporter,
        resource: Resource,
        clock: Clock,
        *,
        batch_size: int = 100,
        backoff: Backoff | None = None,
    ) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._exporter = exporter
        self._resource = resource
        self._clock = clock
        self._batch_size = batch_size
        self._backoff = backoff or Backoff(base_s=2, max_s=300)

    async def run_once(self) -> int:
        """Envía un lote. Devuelve cuántos registros quedaron confirmados."""
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(OutboxRow)
                    .where(OutboxRow.sent_at.is_(None), OutboxRow.next_attempt_at <= now)
                    .order_by(OutboxRow.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not rows:
                return 0
            records = [self._record(row) for row in rows]
            result = await asyncio.to_thread(self._exporter.export, records)
            ids = [row.id for row in rows]
            if result is LogRecordExportResult.SUCCESS:
                await session.execute(
                    update(OutboxRow).where(OutboxRow.id.in_(ids)).values(sent_at=now)
                )
                return len(rows)
            attempts = max(row.attempts for row in rows)
            await session.execute(
                update(OutboxRow)
                .where(OutboxRow.id.in_(ids))
                .values(
                    attempts=OutboxRow.attempts + 1,
                    next_attempt_at=now + self._backoff.delay(attempts),
                    last_error="el Collector no confirmó la exportación",
                )
            )
            logger.warning("el Collector no confirmó %d registros; se reintentará", len(rows))
            return 0

    def _record(self, row: OutboxRow) -> ReadableLogRecord:
        mapped = to_log(outbox_event_from_json(row.payload))
        context = None
        if row.trace_id and row.span_id:
            span_context = SpanContext(
                trace_id=int(row.trace_id, 16),
                span_id=int(row.span_id, 16),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            context = trace.set_span_in_context(NonRecordingSpan(span_context))
        record = LogRecord(
            timestamp=mapped.timestamp_ns,
            observed_timestamp=int(row.created_at.timestamp() * 1_000_000_000),
            context=context,
            severity_text=mapped.severity_text,
            severity_number=mapped.severity_number,
            body=mapped.body,
            attributes=mapped.attributes,
            event_name=mapped.event_name,
        )
        return ReadableLogRecord(record, self._resource, SCOPE)

    async def run_forever(self, stop: asyncio.Event, idle_sleep_s: float = 1.0) -> None:
        while not stop.is_set():
            sent = await self.run_once()
            if not sent:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=idle_sleep_s)

    def shutdown(self) -> None:
        self._exporter.shutdown()  # type: ignore[no-untyped-call]
