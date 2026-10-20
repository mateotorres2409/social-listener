"""Retención configurable por proyecto y supresión por solicitud del titular."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.application.ports.outbound.services import Clock, Pseudonymizer
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.application.use_cases.export.assemble import author_pseudonym_of
from social_listener.domain.mentions.model import Channel
from social_listener.domain.shared.types import ProjectId


def author_pseudonym(pseudonymizer: Pseudonymizer, channel: Channel, external_id: str) -> str:
    return author_pseudonym_of(pseudonymizer, channel.value, external_id)


@dataclass(frozen=True, slots=True)
class ProjectRetention:
    project_id: str
    mentions_purged: int
    authors_deleted: int


@dataclass
class RetentionReport:
    projects: list[ProjectRetention] = field(default_factory=list)


class ApplyRetention:
    """Elimina el texto original y los datos personales de lo que venció.

    Conserva las menciones anonimizadas, su clasificación y los agregados.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self) -> RetentionReport:
        report = RetentionReport()
        now = self._clock.now()
        async with self._uow_factory() as uow:
            projects = list(await uow.projects.list_all())
        for project in projects:
            before = now - timedelta(days=project.retention_days)
            async with self._uow_factory() as uow:
                purged = await uow.mentions.purge_personal_data_before(project.id, before)
                deleted = await uow.authors.delete_inactive_before(project.id, before)
                await uow.audit.record(
                    AuditEntry(
                        action=f"retencion menciones={purged} autores={deleted}",
                        subject=f"proyecto:{project.id}",
                        occurred_at=now,
                        actor="retencion",
                    )
                )
                await uow.commit()
            report.projects.append(ProjectRetention(project.id, purged, deleted))
        return report


@dataclass(frozen=True, slots=True)
class SuppressionResult:
    found: bool
    mentions_anonymized: int = 0


class SuppressAuthorData:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, pseudonymizer: Pseudonymizer, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._pseudonymizer = pseudonymizer
        self._clock = clock

    async def execute(
        self, project_id: ProjectId, channel: Channel, external_id: str, *, actor: str
    ) -> SuppressionResult:
        subject = author_pseudonym(self._pseudonymizer, channel, external_id)
        async with self._uow_factory() as uow:
            author = await uow.authors.get_by_external(project_id, channel, external_id)
            anonymized = 0
            if author is not None:
                anonymized = await uow.mentions.purge_personal_data_of_author(author.id)
                await uow.identity_results.delete(author.id)
                await uow.authors.delete(author.id)
            await uow.audit.record(
                AuditEntry(
                    action="supresion" if author is not None else "supresion_sin_datos",
                    subject=subject,
                    occurred_at=self._clock.now(),
                    actor=actor,
                )
            )
            await uow.commit()
        return SuppressionResult(found=author is not None, mentions_anonymized=anonymized)
