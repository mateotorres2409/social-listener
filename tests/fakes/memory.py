"""Implementaciones en memoria de los puertos de persistencia.

``InMemoryDatabase`` guarda el estado confirmado. Cada ``InMemoryUnitOfWork``
trabaja sobre una copia y solo la publica al hacer ``commit``, lo que imita la
semántica transaccional del adaptador real.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.export import OutboxEvent
from social_listener.application.dto.pipeline import DeadLetter, Job, Stage
from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.application.ports.outbound.services import Clock
from social_listener.domain.authors.model import Author, Thread
from social_listener.domain.classification.model import (
    Dimension,
    MentionClassification,
    MentionType,
    Taxonomy,
    Topic,
)
from social_listener.domain.identity.model import IdentityResult
from social_listener.domain.mentions.model import (
    ANONYMIZED_AUTHOR,
    Channel,
    IncomingMention,
    ListeningQuery,
    Mention,
)
from social_listener.domain.privacy.model import MaskedText
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.types import (
    AuthorId,
    ListeningQueryId,
    MentionId,
    ProjectId,
    ThreadId,
)

COMPLAINT_TYPES = {MentionType.QUEJA.value, MentionType.RECLAMO.value}


@dataclass
class _StoredMention:
    mention: Mention
    raw_text: str | None
    author_id: AuthorId | None = None
    thread_id: ThreadId | None = None
    mention_type: str | None = None


def _anonymize(stored: _StoredMention) -> None:
    stored.raw_text = None
    stored.author_id = None
    stored.mention = replace(
        stored.mention,
        data=replace(stored.mention.data, author=ANONYMIZED_AUTHOR),
    )


@dataclass
class _StoredJob:
    job: Job
    available_at: datetime
    status: str = "pendiente"  # pendiente | tomado | terminado


@dataclass
class State:
    projects: dict[ProjectId, Project] = field(default_factory=dict)
    model_chains: dict[ProjectId, tuple[ModelRoute, ...]] = field(default_factory=dict)
    queries: dict[ListeningQueryId, ListeningQuery] = field(default_factory=dict)
    checkpoints: dict[tuple[ListeningQueryId, str], str] = field(default_factory=dict)
    mentions: dict[MentionId, _StoredMention] = field(default_factory=dict)
    classifications: dict[MentionId, MentionClassification] = field(default_factory=dict)
    taxonomies: dict[ProjectId | None, list[Taxonomy]] = field(default_factory=dict)
    topics: dict[tuple[ProjectId, str], Topic] = field(default_factory=dict)
    authors: dict[AuthorId, Author] = field(default_factory=dict)
    threads: dict[ThreadId, Thread] = field(default_factory=dict)
    identity_results: dict[AuthorId, IdentityResult] = field(default_factory=dict)
    dead_letters: dict[str, DeadLetter] = field(default_factory=dict)
    jobs: dict[str, _StoredJob] = field(default_factory=dict)
    outbox: list[OutboxEvent] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)


class InMemoryDatabase:
    def __init__(self, clock: Clock, *, max_attempts: int = 5) -> None:
        self.state = State()
        self.clock = clock
        self.max_attempts = max_attempts

    def unit_of_work(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)

    def __call__(self) -> InMemoryUnitOfWork:
        return InMemoryUnitOfWork(self)


class _Repo:
    def __init__(self, uow: InMemoryUnitOfWork) -> None:
        self._uow = uow

    @property
    def _s(self) -> State:
        return self._uow.working


class InMemoryProjectRepository(_Repo):
    async def get(self, project_id: ProjectId) -> Project | None:
        return self._s.projects.get(project_id)

    async def save(self, project: Project) -> None:
        self._s.projects[project.id] = project

    async def list_all(self) -> Sequence[Project]:
        return list(self._s.projects.values())


class InMemoryModelChainRepository(_Repo):
    async def get(self, project_id: ProjectId) -> Sequence[ModelRoute]:
        return self._s.model_chains.get(project_id, ())

    async def save(self, project_id: ProjectId, chain: Sequence[ModelRoute]) -> None:
        self._s.model_chains[project_id] = tuple(chain)


class InMemoryListeningQueryRepository(_Repo):
    async def add(self, query: ListeningQuery) -> None:
        self._s.queries[query.id] = query

    async def get(self, query_id: ListeningQueryId) -> ListeningQuery | None:
        return self._s.queries.get(query_id)

    async def save(self, query: ListeningQuery) -> None:
        self._s.queries[query.id] = query

    async def delete(self, query_id: ListeningQueryId) -> bool:
        return self._s.queries.pop(query_id, None) is not None

    async def list_by_project(self, project_id: ProjectId) -> Sequence[ListeningQuery]:
        return [q for q in self._s.queries.values() if q.project_id == project_id]

    async def list_active(self) -> Sequence[ListeningQuery]:
        return [q for q in self._s.queries.values() if q.is_active]


class InMemoryCheckpointStore(_Repo):
    async def get(self, query_id: ListeningQueryId, connector: str) -> str | None:
        return self._s.checkpoints.get((query_id, connector))

    async def save(self, query_id: ListeningQueryId, connector: str, checkpoint: str) -> None:
        self._s.checkpoints[(query_id, connector)] = checkpoint


class InMemoryMentionRepository(_Repo):
    async def add_or_get(
        self, project_id: ProjectId, incoming: IncomingMention, query_id: ListeningQueryId
    ) -> tuple[Mention, bool]:
        existing = await self.find_by_external(incoming.data.channel, incoming.data.external_id)
        if existing is not None:
            stored = self._s.mentions[existing.id]
            stored.mention = replace(
                stored.mention, query_ids=stored.mention.query_ids | {query_id}
            )
            return stored.mention, False
        mention = Mention(
            id=MentionId(str(uuid.uuid4())),
            project_id=project_id,
            data=incoming.data,
            query_ids=frozenset({query_id}),
        )
        self._s.mentions[mention.id] = _StoredMention(mention=mention, raw_text=incoming.raw_text)
        return mention, True

    async def get(self, mention_id: MentionId) -> Mention | None:
        stored = self._s.mentions.get(mention_id)
        return stored.mention if stored else None

    async def find_by_external(self, channel: Channel, external_id: str) -> Mention | None:
        for stored in self._s.mentions.values():
            data = stored.mention.data
            if data.channel == channel and data.external_id == external_id:
                return stored.mention
        return None

    async def get_raw_text(self, mention_id: MentionId) -> str | None:
        stored = self._s.mentions.get(mention_id)
        return stored.raw_text if stored else None

    async def save_protection(self, mention_id: MentionId, text: MaskedText) -> None:
        stored = self._s.mentions[mention_id]
        stored.mention = replace(stored.mention, text=text)

    async def assign_author(
        self, mention_id: MentionId, author_id: AuthorId, thread_id: ThreadId | None
    ) -> None:
        stored = self._s.mentions[mention_id]
        stored.author_id = author_id
        stored.thread_id = thread_id

    async def author_of(self, mention_id: MentionId) -> AuthorId | None:
        stored = self._s.mentions.get(mention_id)
        return stored.author_id if stored else None

    async def thread_of(self, mention_id: MentionId) -> ThreadId | None:
        stored = self._s.mentions.get(mention_id)
        return stored.thread_id if stored else None

    async def list_by_author(self, author_id: AuthorId) -> Sequence[Mention]:
        found = [s.mention for s in self._s.mentions.values() if s.author_id == author_id]
        return sorted(found, key=lambda m: m.data.published_at)

    async def reassign_thread(self, source: ThreadId, target: ThreadId) -> int:
        moved = 0
        for stored in self._s.mentions.values():
            if stored.thread_id == source:
                stored.thread_id = target
                moved += 1
        return moved

    async def purge_personal_data_before(self, project_id: ProjectId, before: datetime) -> int:
        purged = 0
        for stored in self._s.mentions.values():
            mention = stored.mention
            if mention.project_id == project_id and mention.data.published_at < before:
                if stored.raw_text is not None or stored.author_id is not None:
                    purged += 1
                _anonymize(stored)
        return purged

    async def purge_personal_data_of_author(self, author_id: AuthorId) -> int:
        purged = 0
        for stored in self._s.mentions.values():
            if stored.author_id == author_id:
                _anonymize(stored)
                purged += 1
        return purged


class InMemoryClassificationRepository(_Repo):
    async def save(self, classification: MentionClassification) -> None:
        self._s.classifications[classification.mention_id] = classification
        stored = self._s.mentions.get(classification.mention_id)
        if stored is not None:
            stored.mention_type = classification.value_of(Dimension.TIPO)

    async def get(self, mention_id: MentionId) -> MentionClassification | None:
        return self._s.classifications.get(mention_id)


class InMemoryTaxonomyRepository(_Repo):
    async def get_active(self, project_id: ProjectId) -> Taxonomy | None:
        versions = self._s.taxonomies.get(project_id) or self._s.taxonomies.get(None)
        return versions[-1] if versions else None

    async def publish(self, taxonomy: Taxonomy) -> None:
        self._s.taxonomies.setdefault(taxonomy.project_id, []).append(taxonomy)


class InMemoryTopicRepository(_Repo):
    async def most_frequent(self, project_id: ProjectId, limit: int) -> Sequence[Topic]:
        topics = [t for (p, _), t in self._s.topics.items() if p == project_id]
        return sorted(topics, key=lambda t: (-t.mentions_count, t.slug))[:limit]

    async def get(self, project_id: ProjectId, slug: str) -> Topic | None:
        return self._s.topics.get((project_id, slug))

    async def record_use(self, topic: Topic) -> Topic:
        key = (topic.project_id, topic.slug)
        current = self._s.topics.get(key)
        updated = replace(
            current or topic, mentions_count=(current.mentions_count if current else 0) + 1
        )
        self._s.topics[key] = updated
        return updated


class InMemoryAuthorRepository(_Repo):
    async def get(self, author_id: AuthorId) -> Author | None:
        return self._s.authors.get(author_id)

    async def get_by_external(
        self, project_id: ProjectId, channel: Channel, external_id: str
    ) -> Author | None:
        for author in self._s.authors.values():
            if (author.project_id, author.channel, author.external_id) == (
                project_id,
                channel,
                external_id,
            ):
                return author
        return None

    async def save(self, author: Author) -> None:
        self._s.authors[author.id] = author

    async def delete(self, author_id: AuthorId) -> bool:
        return self._s.authors.pop(author_id, None) is not None

    async def delete_inactive_before(self, project_id: ProjectId, before: datetime) -> int:
        stale = [
            a.id
            for a in self._s.authors.values()
            if a.project_id == project_id and a.last_seen_at < before
        ]
        for author_id in stale:
            del self._s.authors[author_id]
            self._s.identity_results.pop(author_id, None)
            for stored in self._s.mentions.values():
                if stored.author_id == author_id:
                    stored.author_id = None
        return len(stale)

    async def complaints_since(self, author_id: AuthorId, since: datetime) -> int:
        return sum(
            1
            for s in self._s.mentions.values()
            if s.author_id == author_id
            and s.mention_type in COMPLAINT_TYPES
            and s.mention.data.published_at >= since
        )


class InMemoryThreadRepository(_Repo):
    async def get(self, thread_id: ThreadId) -> Thread | None:
        return self._s.threads.get(thread_id)

    async def get_by_root(
        self, project_id: ProjectId, channel: Channel, root_external_id: str
    ) -> Thread | None:
        for thread in self._s.threads.values():
            if (thread.project_id, thread.channel, thread.root_external_id) == (
                project_id,
                channel,
                root_external_id,
            ):
                return thread
        return None

    async def save(self, thread: Thread) -> None:
        self._s.threads[thread.id] = thread

    async def delete(self, thread_id: ThreadId) -> None:
        self._s.threads.pop(thread_id, None)


class InMemoryIdentityResultRepository(_Repo):
    async def get(self, author_id: AuthorId) -> IdentityResult | None:
        return self._s.identity_results.get(author_id)

    async def save(self, result: IdentityResult) -> None:
        self._s.identity_results[result.author_id] = result

    async def delete(self, author_id: AuthorId) -> bool:
        return self._s.identity_results.pop(author_id, None) is not None


class InMemoryDeadLetterRepository(_Repo):
    async def add(self, letter: DeadLetter) -> None:
        self._s.dead_letters[letter.id] = letter

    async def get(self, letter_id: str) -> DeadLetter | None:
        return self._s.dead_letters.get(letter_id)

    async def list_recent(self, limit: int) -> Sequence[DeadLetter]:
        letters = sorted(self._s.dead_letters.values(), key=lambda d: d.created_at, reverse=True)
        return letters[:limit]

    async def remove(self, letter_id: str) -> None:
        self._s.dead_letters.pop(letter_id, None)


class InMemoryStageQueue(_Repo):
    async def enqueue(
        self,
        stage: Stage,
        subject_id: str,
        *,
        available_at: datetime | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        for stored in self._s.jobs.values():
            if (
                stored.job.stage == stage
                and stored.job.subject_id == subject_id
                and stored.status != "terminado"
            ):
                return
        job = Job(
            id=str(uuid.uuid4()), stage=stage, subject_id=subject_id, payload=dict(payload or {})
        )
        when = available_at or self._uow.db.clock.now()
        self._s.jobs[job.id] = _StoredJob(job=job, available_at=when)

    async def claim(self, stage: Stage, limit: int) -> Sequence[Job]:
        now = self._uow.db.clock.now()
        ready = sorted(
            (
                s
                for s in self._s.jobs.values()
                if s.job.stage == stage and s.status == "pendiente" and s.available_at <= now
            ),
            key=lambda s: s.available_at,
        )[:limit]
        for stored in ready:
            stored.status = "tomado"
        return [s.job for s in ready]

    async def complete(self, job: Job) -> None:
        self._s.jobs[job.id].status = "terminado"

    async def fail(self, job: Job, reason: str, *, retry_at: datetime | None) -> bool:
        stored = self._s.jobs[job.id]
        attempts = stored.job.attempts + 1
        if retry_at is not None and attempts < self._uow.db.max_attempts:
            stored.job = replace(stored.job, attempts=attempts)
            stored.available_at = retry_at
            stored.status = "pendiente"
            return False
        del self._s.jobs[job.id]
        letter = DeadLetter(
            id=str(uuid.uuid4()),
            stage=job.stage,
            reason=reason,
            attempts=attempts,
            created_at=self._uow.db.clock.now(),
            subject_id=job.subject_id,
            payload=dict(job.payload),
        )
        self._s.dead_letters[letter.id] = letter
        return True

    # Utilidades de prueba (no forman parte del puerto)
    def pending(self, stage: Stage) -> list[Job]:
        return [
            s.job for s in self._s.jobs.values() if s.job.stage == stage and s.status == "pendiente"
        ]


class InMemoryOutbox(_Repo):
    async def add(self, event: OutboxEvent) -> None:
        self._s.outbox.append(event)


class InMemoryAuditLog(_Repo):
    async def record(self, entry: AuditEntry) -> None:
        self._s.audit.append(entry)

    async def list_recent(self, limit: int) -> Sequence[AuditEntry]:
        return list(reversed(self._s.audit))[:limit]


class InMemoryUnitOfWork:
    def __init__(self, db: InMemoryDatabase) -> None:
        self.db = db
        self.working = copy.deepcopy(db.state)
        self.committed = False
        self.projects = InMemoryProjectRepository(self)
        self.model_chains = InMemoryModelChainRepository(self)
        self.queries = InMemoryListeningQueryRepository(self)
        self.checkpoints = InMemoryCheckpointStore(self)
        self.mentions = InMemoryMentionRepository(self)
        self.classifications = InMemoryClassificationRepository(self)
        self.taxonomies = InMemoryTaxonomyRepository(self)
        self.topics = InMemoryTopicRepository(self)
        self.authors = InMemoryAuthorRepository(self)
        self.threads = InMemoryThreadRepository(self)
        self.identity_results = InMemoryIdentityResultRepository(self)
        self.dead_letters = InMemoryDeadLetterRepository(self)
        self.jobs = InMemoryStageQueue(self)
        self.outbox = InMemoryOutbox(self)
        self.audit = InMemoryAuditLog(self)

    async def __aenter__(self) -> Self:
        self.working = copy.deepcopy(self.db.state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.rollback()

    async def commit(self) -> None:
        self.db.state = copy.deepcopy(self.working)
        self.committed = True

    async def rollback(self) -> None:
        self.working = copy.deepcopy(self.db.state)


class FixedClock:
    """Reloj controlable para pruebas."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now = self._now + timedelta(**delta)


def _verificar_protocolos() -> None:  # pragma: no cover - solo para mypy
    from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory

    _factory: UnitOfWorkFactory = InMemoryDatabase(FixedClock(datetime.now()))


def _verificar_uow() -> None:  # pragma: no cover - solo para mypy
    from social_listener.application.ports.outbound.unit_of_work import UnitOfWork

    _uow: UnitOfWork = InMemoryDatabase(FixedClock(datetime.now()))()
