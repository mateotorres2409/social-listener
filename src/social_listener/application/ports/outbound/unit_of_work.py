"""Unidad de trabajo: agrupa repositorios en una transacción."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from social_listener.application.ports.outbound.pipeline import (
    AuditLog,
    EnrichedMentionOutbox,
    StageQueue,
)
from social_listener.application.ports.outbound.repositories import (
    AuthorRepository,
    CheckpointStore,
    ClassificationRepository,
    DeadLetterRepository,
    IdentityResultRepository,
    ListeningQueryRepository,
    MentionRepository,
    ModelChainRepository,
    ProjectRepository,
    TaxonomyRepository,
    ThreadRepository,
    TopicRepository,
)


class UnitOfWork(Protocol):
    @property
    def projects(self) -> ProjectRepository: ...
    @property
    def model_chains(self) -> ModelChainRepository: ...
    @property
    def queries(self) -> ListeningQueryRepository: ...
    @property
    def checkpoints(self) -> CheckpointStore: ...
    @property
    def mentions(self) -> MentionRepository: ...
    @property
    def classifications(self) -> ClassificationRepository: ...
    @property
    def taxonomies(self) -> TaxonomyRepository: ...
    @property
    def topics(self) -> TopicRepository: ...
    @property
    def authors(self) -> AuthorRepository: ...
    @property
    def threads(self) -> ThreadRepository: ...
    @property
    def identity_results(self) -> IdentityResultRepository: ...
    @property
    def dead_letters(self) -> DeadLetterRepository: ...
    @property
    def jobs(self) -> StageQueue: ...
    @property
    def outbox(self) -> EnrichedMentionOutbox: ...
    @property
    def audit(self) -> AuditLog: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Si hubo excepción hace rollback; si no, lo que no se confirmó se descarta."""
        ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...
