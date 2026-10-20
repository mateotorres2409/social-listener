"""Etapa de protección: enmascara los datos sensibles del texto original."""

from __future__ import annotations

from social_listener.application.dto.pipeline import Job
from social_listener.application.ports.inbound.stages import (
    ADVANCE,
    PermanentStageError,
    StageOutcome,
)
from social_listener.application.ports.outbound.services import SensitiveDataDetector
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.domain.privacy.policies import mask
from social_listener.domain.shared.types import MentionId


class ProtectMention:
    """Único caso de uso, junto con la retención, que accede al texto original."""

    def __init__(self, detector: SensitiveDataDetector) -> None:
        self._detector = detector

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        mention_id = MentionId(job.subject_id)
        raw_text = await uow.mentions.get_raw_text(mention_id)
        if raw_text is None:
            raise PermanentStageError("la mención no existe o su texto ya fue suprimido")
        masked = mask(raw_text, self._detector.detect(raw_text))
        await uow.mentions.save_protection(mention_id, masked)
        return ADVANCE
