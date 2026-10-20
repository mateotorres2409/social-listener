"""Unidad de trabajo sobre una sesión asíncrona de SQLAlchemy."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from social_listener.adapters.outbound.persistence.queue import (
    PgOutbox,
    PgStageQueue,
    TraceContext,
    no_trace_context,
)
from social_listener.adapters.outbound.persistence.repositories import (
    PgAuditLog,
    PgAuthorRepository,
    PgCheckpointStore,
    PgClassificationRepository,
    PgDeadLetterRepository,
    PgIdentityResultRepository,
    PgListeningQueryRepository,
    PgMentionRepository,
    PgModelChainRepository,
    PgProjectRepository,
    PgTaxonomyRepository,
    PgThreadRepository,
    PgTopicRepository,
)
from social_listener.application.ports.outbound.services import Clock


def create_engine(url: str, *, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)


class SqlAlchemyUnitOfWork:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Clock,
        *,
        max_attempts: int,
        trace_context: TraceContext = no_trace_context,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._max_attempts = max_attempts
        self._trace_context = trace_context
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("la unidad de trabajo no está abierta")
        return self._session

    async def __aenter__(self) -> Self:
        session = self._sessions()
        self._session = session
        self.projects = PgProjectRepository(session)
        self.model_chains = PgModelChainRepository(session)
        self.queries = PgListeningQueryRepository(session)
        self.checkpoints = PgCheckpointStore(session)
        self.mentions = PgMentionRepository(session)
        self.classifications = PgClassificationRepository(session)
        self.taxonomies = PgTaxonomyRepository(session)
        self.topics = PgTopicRepository(session)
        self.authors = PgAuthorRepository(session)
        self.threads = PgThreadRepository(session)
        self.identity_results = PgIdentityResultRepository(session)
        self.dead_letters = PgDeadLetterRepository(session)
        self.jobs = PgStageQueue(session, self._clock, max_attempts=self._max_attempts)
        self.outbox = PgOutbox(session, self._clock, self._trace_context)
        self.audit = PgAuditLog(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(
        self,
        engine: AsyncEngine,
        clock: Clock,
        *,
        max_attempts: int,
        trace_context: TraceContext = no_trace_context,
    ) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._clock = clock
        self._max_attempts = max_attempts
        self._trace_context = trace_context

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            self._sessions,
            self._clock,
            max_attempts=self._max_attempts,
            trace_context=self._trace_context,
        )


def _verificar_protocolos(factory: SqlAlchemyUnitOfWorkFactory) -> None:  # pragma: no cover
    from social_listener.application.ports.outbound.unit_of_work import (
        UnitOfWork,
        UnitOfWorkFactory,
    )

    _f: UnitOfWorkFactory = factory
    _u: UnitOfWork = factory()
