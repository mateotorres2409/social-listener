"""Repositorios PostgreSQL que implementan los puertos de persistencia."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from social_listener.adapters.outbound.persistence import codecs
from social_listener.adapters.outbound.persistence.models import (
    AuditRow,
    AuthorRow,
    CheckpointRow,
    ClassificationRow,
    DeadLetterRow,
    IdentityResultRow,
    ListeningQueryRow,
    MentionQueryRow,
    MentionRow,
    ModelChainRow,
    ProjectRow,
    TaxonomyRow,
    ThreadRow,
    TopicRow,
    UsernameHistoryRow,
)
from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.pipeline import DeadLetter, Stage
from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.domain.authors.model import Author, Thread, UsernameChange
from social_listener.domain.classification.model import (
    Dimension,
    MentionClassification,
    MentionType,
    Taxonomy,
    Topic,
)
from social_listener.domain.identity.model import IdentityResult, IdentityStatus
from social_listener.domain.mentions.model import (
    ANONYMIZED,
    AuthorSnapshot,
    Channel,
    ContentFormat,
    Engagement,
    IncomingMention,
    ListeningQuery,
    Mention,
    MentionData,
    QueryStatus,
)
from social_listener.domain.privacy.model import MaskedText, SensitiveDataType
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.types import (
    AuthorId,
    ListeningQueryId,
    MentionId,
    ProjectId,
    ThreadId,
)

COMPLAINT_TYPES = (MentionType.QUEJA.value, MentionType.RECLAMO.value)


class _Repo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session


class PgProjectRepository(_Repo):
    async def get(self, project_id: ProjectId) -> Project | None:
        row = await self._session.get(ProjectRow, project_id)
        return _project(row) if row else None

    async def save(self, project: Project) -> None:
        values = {
            "id": project.id,
            "name": project.name,
            "retention_days": project.retention_days,
            "review_confidence_threshold": project.review_confidence_threshold,
            "impact": codecs.impact_to_json(project.impact),
            "recurrence": codecs.recurrence_to_json(project.recurrence),
            "official_accounts": codecs.official_accounts_to_json(project.official_accounts),
        }
        stmt = insert(ProjectRow).values(values)
        await self._session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))

    async def list_all(self) -> Sequence[Project]:
        rows = await self._session.scalars(select(ProjectRow).order_by(ProjectRow.name))
        return [_project(r) for r in rows]


def _project(row: ProjectRow) -> Project:
    return Project(
        id=ProjectId(row.id),
        name=row.name,
        retention_days=row.retention_days,
        review_confidence_threshold=row.review_confidence_threshold,
        impact=codecs.impact_from_json(row.impact),
        recurrence=codecs.recurrence_from_json(row.recurrence),
        official_accounts=codecs.official_accounts_from_json(row.official_accounts),
    )


class PgModelChainRepository(_Repo):
    async def get(self, project_id: ProjectId) -> Sequence[ModelRoute]:
        row = await self._session.get(ModelChainRow, project_id)
        return tuple(codecs.model_route_from_json(r) for r in row.chain) if row else ()

    async def save(self, project_id: ProjectId, chain: Sequence[ModelRoute]) -> None:
        data = [codecs.model_route_to_json(r) for r in chain]
        stmt = insert(ModelChainRow).values(project_id=project_id, chain=data)
        await self._session.execute(
            stmt.on_conflict_do_update(index_elements=["project_id"], set_={"chain": data})
        )


class PgListeningQueryRepository(_Repo):
    async def add(self, query: ListeningQuery) -> None:
        self._session.add(ListeningQueryRow(**_query_values(query)))
        await self._session.flush()

    async def get(self, query_id: ListeningQueryId) -> ListeningQuery | None:
        row = await self._session.get(ListeningQueryRow, query_id)
        return _query(row) if row else None

    async def save(self, query: ListeningQuery) -> None:
        values = _query_values(query)
        stmt = insert(ListeningQueryRow).values(values)
        await self._session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))

    async def delete(self, query_id: ListeningQueryId) -> bool:
        result = await self._session.execute(
            delete(ListeningQueryRow).where(ListeningQueryRow.id == query_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_by_project(self, project_id: ProjectId) -> Sequence[ListeningQuery]:
        rows = await self._session.scalars(
            select(ListeningQueryRow)
            .where(ListeningQueryRow.project_id == project_id)
            .order_by(ListeningQueryRow.name)
        )
        return [_query(r) for r in rows]

    async def list_active(self) -> Sequence[ListeningQuery]:
        rows = await self._session.scalars(
            select(ListeningQueryRow)
            .where(ListeningQueryRow.status == QueryStatus.ACTIVE.value)
            .order_by(ListeningQueryRow.name)
        )
        return [_query(r) for r in rows]


def _query_values(query: ListeningQuery) -> dict[str, object]:
    return {
        "id": query.id,
        "project_id": query.project_id,
        "name": query.name,
        "channels": list(query.channels),
        "terms": list(query.terms),
        "accounts": list(query.accounts),
        "hashtags": list(query.hashtags),
        "languages": list(query.languages),
        "status": query.status.value,
    }


def _query(row: ListeningQueryRow) -> ListeningQuery:
    return ListeningQuery(
        id=ListeningQueryId(row.id),
        project_id=ProjectId(row.project_id),
        name=row.name,
        channels=tuple(row.channels),
        terms=tuple(row.terms),
        accounts=tuple(row.accounts),
        hashtags=tuple(row.hashtags),
        languages=tuple(row.languages),
        status=QueryStatus(row.status),
    )


class PgCheckpointStore(_Repo):
    async def get(self, query_id: ListeningQueryId, connector: str) -> str | None:
        row = await self._session.get(CheckpointRow, (query_id, connector))
        return row.checkpoint if row else None

    async def save(self, query_id: ListeningQueryId, connector: str, checkpoint: str) -> None:
        stmt = insert(CheckpointRow).values(
            query_id=query_id, connector=connector, checkpoint=checkpoint
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=["query_id", "connector"], set_={"checkpoint": checkpoint}
            )
        )


class PgMentionRepository(_Repo):
    async def add_or_get(
        self, project_id: ProjectId, incoming: IncomingMention, query_id: ListeningQueryId
    ) -> tuple[Mention, bool]:
        data = incoming.data
        stmt = (
            insert(MentionRow)
            .values(
                id=str(uuid.uuid4()),
                project_id=project_id,
                channel=data.channel.value,
                external_id=data.external_id,
                url=data.url,
                language=data.language,
                published_at=data.published_at,
                format=data.format.value,
                parent_external_id=data.parent_external_id,
                author_external_id=data.author.external_id,
                author_username=data.author.username,
                author_display_name=data.author.display_name,
                author_followers=data.author.followers,
                author_verified=data.author.verified,
                likes=data.engagement.likes,
                shares=data.engagement.shares,
                replies=data.engagement.replies,
                views=data.engagement.views,
                raw_text=incoming.raw_text,
            )
            .on_conflict_do_nothing(index_elements=["channel", "external_id"])
            .returning(MentionRow.id)
        )
        created_id = (await self._session.execute(stmt)).scalar_one_or_none()
        mention_id = created_id or await self._session.scalar(
            select(MentionRow.id).where(
                MentionRow.channel == data.channel.value,
                MentionRow.external_id == data.external_id,
            )
        )
        if mention_id is None:  # pragma: no cover - borrado concurrente
            raise RuntimeError("la mención desapareció durante la inserción")
        await self._session.execute(
            insert(MentionQueryRow)
            .values(mention_id=mention_id, query_id=query_id)
            .on_conflict_do_nothing()
        )
        mention = await self.get(MentionId(mention_id))
        if mention is None:  # pragma: no cover - borrado concurrente
            raise RuntimeError("la mención desapareció durante la inserción")
        return mention, created_id is not None

    async def get(self, mention_id: MentionId) -> Mention | None:
        row = await self._session.get(MentionRow, mention_id, populate_existing=True)
        return await self._mention(row) if row else None

    async def find_by_external(self, channel: Channel, external_id: str) -> Mention | None:
        row = await self._session.scalar(
            select(MentionRow).where(
                MentionRow.channel == channel.value, MentionRow.external_id == external_id
            )
        )
        return await self._mention(row) if row else None

    async def get_raw_text(self, mention_id: MentionId) -> str | None:
        return await self._session.scalar(
            select(MentionRow.raw_text).where(MentionRow.id == mention_id)
        )

    async def save_protection(self, mention_id: MentionId, text: MaskedText) -> None:
        await self._session.execute(
            update(MentionRow)
            .where(MentionRow.id == mention_id)
            .values(masked_text=text.value, sensitive_types=sorted(t.value for t in text.detected))
        )

    async def assign_author(
        self, mention_id: MentionId, author_id: AuthorId, thread_id: ThreadId | None
    ) -> None:
        await self._session.execute(
            update(MentionRow)
            .where(MentionRow.id == mention_id)
            .values(author_id=author_id, thread_id=thread_id)
        )

    async def author_of(self, mention_id: MentionId) -> AuthorId | None:
        value = await self._session.scalar(
            select(MentionRow.author_id).where(MentionRow.id == mention_id)
        )
        return AuthorId(value) if value else None

    async def thread_of(self, mention_id: MentionId) -> ThreadId | None:
        value = await self._session.scalar(
            select(MentionRow.thread_id).where(MentionRow.id == mention_id)
        )
        return ThreadId(value) if value else None

    async def list_by_author(self, author_id: AuthorId) -> Sequence[Mention]:
        rows = await self._session.scalars(
            select(MentionRow)
            .where(MentionRow.author_id == author_id)
            .order_by(MentionRow.published_at)
        )
        return [await self._mention(r) for r in rows.all()]

    async def reassign_thread(self, source: ThreadId, target: ThreadId) -> int:
        result = await self._session.execute(
            update(MentionRow).where(MentionRow.thread_id == source).values(thread_id=target)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def purge_personal_data_before(self, project_id: ProjectId, before: datetime) -> int:
        condition = (
            (MentionRow.project_id == project_id)
            & (MentionRow.published_at < before)
            & ((MentionRow.raw_text.is_not(None)) | (MentionRow.author_id.is_not(None)))
        )
        return await self._anonymize(condition)

    async def purge_personal_data_of_author(self, author_id: AuthorId) -> int:
        return await self._anonymize(MentionRow.author_id == author_id)

    async def _anonymize(self, condition: object) -> int:
        result = await self._session.execute(
            update(MentionRow)
            .where(condition)  # type: ignore[arg-type]
            .values(
                raw_text=None,
                author_id=None,
                author_external_id=ANONYMIZED,
                author_username=ANONYMIZED,
                author_display_name=None,
                author_followers=None,
                author_verified=False,
            )
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def _mention(self, row: MentionRow) -> Mention:
        query_ids = await self._session.scalars(
            select(MentionQueryRow.query_id).where(MentionQueryRow.mention_id == row.id)
        )
        text = (
            MaskedText(
                row.masked_text, frozenset(SensitiveDataType(t) for t in row.sensitive_types)
            )
            if row.masked_text is not None
            else None
        )
        return Mention(
            id=MentionId(row.id),
            project_id=ProjectId(row.project_id),
            data=MentionData(
                channel=Channel(row.channel),
                external_id=row.external_id,
                url=row.url,
                language=row.language,
                published_at=row.published_at,
                author=AuthorSnapshot(
                    external_id=row.author_external_id,
                    username=row.author_username,
                    display_name=row.author_display_name,
                    followers=row.author_followers,
                    verified=row.author_verified,
                ),
                format=ContentFormat(row.format),
                engagement=Engagement(
                    likes=row.likes, shares=row.shares, replies=row.replies, views=row.views
                ),
                parent_external_id=row.parent_external_id,
            ),
            query_ids=frozenset(ListeningQueryId(q) for q in query_ids),
            text=text,
        )


class PgClassificationRepository(_Repo):
    async def save(self, classification: MentionClassification) -> None:
        values = {
            "mention_id": classification.mention_id,
            "dimensions": codecs.dimensions_to_json(classification.values),
            "mention_type": classification.value_of(Dimension.TIPO),
            "taxonomy_version": classification.taxonomy_version,
            "channel_format": classification.channel_format,
            "sentiment_score": classification.sentiment_score,
            "subcategory": classification.subcategory,
            "needs_review": classification.needs_review,
        }
        stmt = insert(ClassificationRow).values(values)
        await self._session.execute(
            stmt.on_conflict_do_update(index_elements=["mention_id"], set_=values)
        )

    async def get(self, mention_id: MentionId) -> MentionClassification | None:
        row = await self._session.get(ClassificationRow, mention_id, populate_existing=True)
        if row is None:
            return None
        return MentionClassification(
            mention_id=MentionId(row.mention_id),
            values=codecs.dimensions_from_json(row.dimensions),
            taxonomy_version=row.taxonomy_version,
            channel_format=row.channel_format,
            sentiment_score=row.sentiment_score,
            subcategory=row.subcategory,
            needs_review=row.needs_review,
        )


class PgTaxonomyRepository(_Repo):
    async def get_active(self, project_id: ProjectId) -> Taxonomy | None:
        for condition in (TaxonomyRow.project_id == project_id, TaxonomyRow.project_id.is_(None)):
            row = await self._session.scalar(
                select(TaxonomyRow).where(condition).order_by(TaxonomyRow.version.desc()).limit(1)
            )
            if row is not None:
                return Taxonomy(
                    version=row.version,
                    categories=codecs.categories_from_json(row.categories),
                    project_id=ProjectId(row.project_id) if row.project_id else None,
                )
        return None

    async def publish(self, taxonomy: Taxonomy) -> None:
        self._session.add(
            TaxonomyRow(
                project_id=taxonomy.project_id,
                version=taxonomy.version,
                categories=codecs.categories_to_json(taxonomy.categories),
            )
        )
        await self._session.flush()


class PgTopicRepository(_Repo):
    async def most_frequent(self, project_id: ProjectId, limit: int) -> Sequence[Topic]:
        rows = await self._session.scalars(
            select(TopicRow)
            .where(TopicRow.project_id == project_id)
            .order_by(TopicRow.mentions_count.desc(), TopicRow.slug)
            .limit(limit)
        )
        return [_topic(r) for r in rows]

    async def get(self, project_id: ProjectId, slug: str) -> Topic | None:
        row = await self._session.get(TopicRow, (project_id, slug), populate_existing=True)
        return _topic(row) if row else None

    async def record_use(self, topic: Topic) -> Topic:
        stmt = insert(TopicRow).values(
            project_id=topic.project_id, slug=topic.slug, label=topic.label, mentions_count=1
        )
        row = (
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["project_id", "slug"],
                    set_={"mentions_count": TopicRow.mentions_count + 1},
                ).returning(TopicRow.mentions_count, TopicRow.label)
            )
        ).one()
        return Topic(topic.project_id, topic.slug, row.label, row.mentions_count)


def _topic(row: TopicRow) -> Topic:
    return Topic(ProjectId(row.project_id), row.slug, row.label, row.mentions_count)


class PgAuthorRepository(_Repo):
    async def get(self, author_id: AuthorId) -> Author | None:
        row = await self._session.get(AuthorRow, author_id, populate_existing=True)
        return await self._author(row) if row else None

    async def get_by_external(
        self, project_id: ProjectId, channel: Channel, external_id: str
    ) -> Author | None:
        row = await self._session.scalar(
            select(AuthorRow).where(
                AuthorRow.project_id == project_id,
                AuthorRow.channel == channel.value,
                AuthorRow.external_id == external_id,
            )
        )
        return await self._author(row) if row else None

    async def save(self, author: Author) -> None:
        values = {
            "id": author.id,
            "project_id": author.project_id,
            "channel": author.channel.value,
            "external_id": author.external_id,
            "username": author.username,
            "display_name": author.display_name,
            "followers": author.followers,
            "verified": author.verified,
            "first_seen_at": author.first_seen_at,
            "last_seen_at": author.last_seen_at,
            "total_mentions": author.total_mentions,
            "type_counts": dict(author.type_counts),
            "sentiment_trend": author.sentiment_trend,
            "recurrent": author.recurrent,
            "is_official": author.is_official,
        }
        stmt = insert(AuthorRow).values(values)
        await self._session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))
        await self._session.execute(
            delete(UsernameHistoryRow).where(UsernameHistoryRow.author_id == author.id)
        )
        if author.previous_usernames:
            await self._session.execute(
                insert(UsernameHistoryRow),
                [
                    {"author_id": author.id, "username": c.username, "replaced_at": c.replaced_at}
                    for c in author.previous_usernames
                ],
            )

    async def delete(self, author_id: AuthorId) -> bool:
        result = await self._session.execute(delete(AuthorRow).where(AuthorRow.id == author_id))
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_inactive_before(self, project_id: ProjectId, before: datetime) -> int:
        result = await self._session.execute(
            delete(AuthorRow).where(
                AuthorRow.project_id == project_id, AuthorRow.last_seen_at < before
            )
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def complaints_since(self, author_id: AuthorId, since: datetime) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(MentionRow)
            .join(ClassificationRow, ClassificationRow.mention_id == MentionRow.id)
            .where(
                MentionRow.author_id == author_id,
                MentionRow.published_at >= since,
                ClassificationRow.mention_type.in_(COMPLAINT_TYPES),
            )
        )
        return int(count or 0)

    async def _author(self, row: AuthorRow) -> Author:
        history = await self._session.scalars(
            select(UsernameHistoryRow)
            .where(UsernameHistoryRow.author_id == row.id)
            .order_by(UsernameHistoryRow.replaced_at, UsernameHistoryRow.id)
        )
        return Author(
            id=AuthorId(row.id),
            project_id=ProjectId(row.project_id),
            channel=Channel(row.channel),
            external_id=row.external_id,
            username=row.username,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            display_name=row.display_name,
            followers=row.followers,
            verified=row.verified,
            total_mentions=row.total_mentions,
            type_counts=dict(row.type_counts),
            sentiment_trend=row.sentiment_trend,
            recurrent=row.recurrent,
            is_official=row.is_official,
            previous_usernames=tuple(UsernameChange(h.username, h.replaced_at) for h in history),
        )


class PgThreadRepository(_Repo):
    async def get(self, thread_id: ThreadId) -> Thread | None:
        row = await self._session.get(ThreadRow, thread_id, populate_existing=True)
        return _thread(row) if row else None

    async def get_by_root(
        self, project_id: ProjectId, channel: Channel, root_external_id: str
    ) -> Thread | None:
        row = await self._session.scalar(
            select(ThreadRow).where(
                ThreadRow.project_id == project_id,
                ThreadRow.channel == channel.value,
                ThreadRow.root_external_id == root_external_id,
            )
        )
        return _thread(row) if row else None

    async def save(self, thread: Thread) -> None:
        values = {
            "id": thread.id,
            "project_id": thread.project_id,
            "channel": thread.channel.value,
            "root_external_id": thread.root_external_id,
            "attended_by_entity": thread.attended_by_entity,
        }
        stmt = insert(ThreadRow).values(values)
        await self._session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=values))

    async def delete(self, thread_id: ThreadId) -> None:
        await self._session.execute(delete(ThreadRow).where(ThreadRow.id == thread_id))


def _thread(row: ThreadRow) -> Thread:
    return Thread(
        id=ThreadId(row.id),
        project_id=ProjectId(row.project_id),
        channel=Channel(row.channel),
        root_external_id=row.root_external_id,
        attended_by_entity=row.attended_by_entity,
    )


class PgIdentityResultRepository(_Repo):
    async def get(self, author_id: AuthorId) -> IdentityResult | None:
        row = await self._session.get(IdentityResultRow, author_id, populate_existing=True)
        if row is None:
            return None
        return IdentityResult(
            author_id=AuthorId(row.author_id),
            status=IdentityStatus(row.status),
            validated_at=row.validated_at,
            username=row.username,
            customer_ref=row.customer_ref,
            confidence=row.confidence,
            attempts=row.attempts,
        )

    async def save(self, result: IdentityResult) -> None:
        values = {
            "author_id": result.author_id,
            "status": result.status.value,
            "username": result.username,
            "customer_ref": result.customer_ref,
            "confidence": result.confidence,
            "validated_at": result.validated_at,
            "attempts": result.attempts,
        }
        stmt = insert(IdentityResultRow).values(values)
        await self._session.execute(
            stmt.on_conflict_do_update(index_elements=["author_id"], set_=values)
        )

    async def delete(self, author_id: AuthorId) -> bool:
        result = await self._session.execute(
            delete(IdentityResultRow).where(IdentityResultRow.author_id == author_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]


class PgDeadLetterRepository(_Repo):
    async def add(self, letter: DeadLetter) -> None:
        self._session.add(
            DeadLetterRow(
                id=letter.id,
                stage=letter.stage.value,
                subject_id=letter.subject_id,
                reason=letter.reason,
                attempts=letter.attempts,
                payload=dict(letter.payload),
                created_at=letter.created_at,
            )
        )
        await self._session.flush()

    async def get(self, letter_id: str) -> DeadLetter | None:
        row = await self._session.get(DeadLetterRow, letter_id, populate_existing=True)
        return _dead_letter(row) if row else None

    async def list_recent(self, limit: int) -> Sequence[DeadLetter]:
        rows = await self._session.scalars(
            select(DeadLetterRow).order_by(DeadLetterRow.created_at.desc()).limit(limit)
        )
        return [_dead_letter(r) for r in rows]

    async def remove(self, letter_id: str) -> None:
        await self._session.execute(delete(DeadLetterRow).where(DeadLetterRow.id == letter_id))


def _dead_letter(row: DeadLetterRow) -> DeadLetter:
    return DeadLetter(
        id=row.id,
        stage=Stage(row.stage),
        reason=row.reason,
        attempts=row.attempts,
        created_at=row.created_at,
        subject_id=row.subject_id,
        payload=dict(row.payload),
    )


class PgAuditLog(_Repo):
    async def record(self, entry: AuditEntry) -> None:
        self._session.add(
            AuditRow(
                action=entry.action,
                subject=entry.subject,
                actor=entry.actor,
                occurred_at=entry.occurred_at,
            )
        )
        await self._session.flush()

    async def list_recent(self, limit: int) -> Sequence[AuditEntry]:
        rows = await self._session.scalars(
            select(AuditRow).order_by(AuditRow.occurred_at.desc(), AuditRow.id.desc()).limit(limit)
        )
        return [AuditEntry(r.action, r.subject, r.occurred_at, r.actor) for r in rows]
