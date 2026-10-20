"""Cola de etapas sobre PostgreSQL (``FOR UPDATE SKIP LOCKED``) y outbox."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from social_listener.adapters.outbound.persistence.codecs import outbox_event_to_json
from social_listener.adapters.outbound.persistence.models import (
    DeadLetterRow,
    JobRow,
    OutboxRow,
)
from social_listener.application.dto.export import OutboxEvent
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.ports.outbound.services import Clock

PENDING = "pendiente"
CLAIMED = "tomado"

TraceContext = Callable[[], tuple[str | None, str | None]]


def no_trace_context() -> tuple[str | None, str | None]:
    return None, None


class PgStageQueue:
    """Cada trabajo se toma y se procesa dentro de la misma transacción.

    Si un worker muere después de confirmar la toma, el trabajo vuelve a estar
    disponible cuando vence ``lease``.
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Clock,
        *,
        max_attempts: int,
        lease: timedelta = timedelta(minutes=5),
    ) -> None:
        self._session = session
        self._clock = clock
        self._max_attempts = max_attempts
        self._lease = lease

    async def enqueue(
        self,
        stage: Stage,
        subject_id: str,
        *,
        available_at: datetime | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        stmt = insert(JobRow).values(
            id=str(uuid.uuid4()),
            stage=stage.value,
            subject_id=subject_id,
            status=PENDING,
            attempts=0,
            available_at=available_at or self._clock.now(),
            payload=dict(payload or {}),
        )
        await self._session.execute(
            stmt.on_conflict_do_nothing(index_elements=["stage", "subject_id"])
        )

    async def claim(self, stage: Stage, limit: int) -> Sequence[Job]:
        now = self._clock.now()
        rows = (
            await self._session.scalars(
                select(JobRow)
                .where(
                    JobRow.stage == stage.value,
                    or_(
                        (JobRow.status == PENDING) & (JobRow.available_at <= now),
                        (JobRow.status == CLAIMED) & (JobRow.claimed_at < now - self._lease),
                    ),
                )
                .order_by(JobRow.available_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not rows:
            return []
        await self._session.execute(
            update(JobRow)
            .where(JobRow.id.in_([r.id for r in rows]))
            .values(status=CLAIMED, claimed_at=now)
        )
        return [
            Job(
                id=r.id,
                stage=Stage(r.stage),
                subject_id=r.subject_id,
                attempts=r.attempts,
                payload=dict(r.payload),
            )
            for r in rows
        ]

    async def complete(self, job: Job) -> None:
        await self._session.execute(delete(JobRow).where(JobRow.id == job.id))

    async def fail(self, job: Job, reason: str, *, retry_at: datetime | None) -> bool:
        attempts = job.attempts + 1
        if retry_at is not None and attempts < self._max_attempts:
            await self._session.execute(
                update(JobRow)
                .where(JobRow.id == job.id)
                .values(
                    status=PENDING,
                    attempts=attempts,
                    available_at=retry_at,
                    claimed_at=None,
                    last_error=reason,
                )
            )
            return False
        await self._session.execute(delete(JobRow).where(JobRow.id == job.id))
        self._session.add(
            DeadLetterRow(
                id=str(uuid.uuid4()),
                stage=job.stage.value,
                subject_id=job.subject_id,
                reason=reason,
                attempts=attempts,
                payload=dict(job.payload),
                created_at=self._clock.now(),
            )
        )
        await self._session.flush()
        return True


class PgOutbox:
    """Guarda los eventos a exportar en la misma transacción que el resultado.

    ``trace_context`` entrega el trace_id y span_id activos para correlacionar el
    LogRecord con la traza del procesamiento.
    """

    def __init__(
        self, session: AsyncSession, clock: Clock, trace_context: TraceContext = no_trace_context
    ) -> None:
        self._session = session
        self._clock = clock
        self._trace_context = trace_context

    async def add(self, event: OutboxEvent) -> None:
        trace_id, span_id = self._trace_context()
        now = self._clock.now()
        self._session.add(
            OutboxRow(
                event_name=event.name.value,
                payload=outbox_event_to_json(event),
                trace_id=trace_id,
                span_id=span_id,
                created_at=now,
                next_attempt_at=now,
                attempts=0,
            )
        )
        await self._session.flush()
