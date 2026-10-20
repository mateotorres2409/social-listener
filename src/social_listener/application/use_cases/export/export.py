"""Etapa de exportación: deja la mención enriquecida en el outbox."""

from __future__ import annotations

from social_listener.application.dto.export import EventName, OutboxEvent
from social_listener.application.dto.pipeline import Job
from social_listener.application.ports.inbound.stages import ADVANCE, StageOutcome
from social_listener.application.ports.outbound.services import Clock
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.application.use_cases.export.assemble import EnrichedMentionAssembler
from social_listener.domain.shared.types import MentionId


class ExportMention:
    """El envío real (OTLP) lo hace el despachador del outbox, fuera de esta transacción."""

    def __init__(self, assembler: EnrichedMentionAssembler, clock: Clock) -> None:
        self._assembler = assembler
        self._clock = clock

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        enriched = await self._assembler.assemble(uow, MentionId(job.subject_id))
        await uow.outbox.add(
            OutboxEvent(
                name=EventName.MENTION_ENRICHED, occurred_at=self._clock.now(), mention=enriched
            )
        )
        return ADVANCE
