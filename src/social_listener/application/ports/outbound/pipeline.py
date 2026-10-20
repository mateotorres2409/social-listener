"""Puertos de la cola de etapas, del outbox y de auditoría."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from social_listener.application.dto.export import OutboxEvent
from social_listener.application.dto.pipeline import Job, Stage


class StageQueue(Protocol):
    async def enqueue(
        self,
        stage: Stage,
        subject_id: str,
        *,
        available_at: datetime | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        """Encola un trabajo. Es idempotente para un mismo (etapa, sujeto) pendiente."""
        ...

    async def claim(self, stage: Stage, limit: int) -> Sequence[Job]:
        """Toma trabajos disponibles sin que otro worker los procese a la vez."""
        ...

    async def complete(self, job: Job) -> None: ...

    async def fail(self, job: Job, reason: str, *, retry_at: datetime | None) -> bool:
        """Registra el fallo. Con ``retry_at`` lo reprograma; sin él (o con los
        reintentos agotados) lo mueve a la cola de errores. Devuelve True si quedó
        en la cola de errores."""
        ...


class EnrichedMentionOutbox(Protocol):
    async def add(self, event: OutboxEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    subject: str
    occurred_at: datetime
    actor: str


class AuditLog(Protocol):
    async def record(self, entry: AuditEntry) -> None: ...
    async def list_recent(self, limit: int) -> Sequence[AuditEntry]: ...
