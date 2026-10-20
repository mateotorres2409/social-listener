"""Modelos de persistencia (SQLAlchemy). Solo los usa este adaptador."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Uuid = UUID(as_uuid=False)
Timestamp = DateTime(timezone=True)
TextArray = ARRAY(Text)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    retention_days: Mapped[int] = mapped_column(Integer)
    review_confidence_threshold: Mapped[float] = mapped_column(Float)
    impact: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recurrence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    official_accounts: Mapped[list[dict[str, str]]] = mapped_column(JSONB)


class ModelChainRow(Base):
    __tablename__ = "model_chains"

    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    chain: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class ListeningQueryRow(Base):
    __tablename__ = "listening_queries"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    channels: Mapped[list[str]] = mapped_column(TextArray)
    terms: Mapped[list[str]] = mapped_column(TextArray)
    accounts: Mapped[list[str]] = mapped_column(TextArray)
    hashtags: Mapped[list[str]] = mapped_column(TextArray)
    languages: Mapped[list[str]] = mapped_column(TextArray)
    status: Mapped[str] = mapped_column(String(20))


class CheckpointRow(Base):
    __tablename__ = "connector_checkpoints"

    query_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("listening_queries.id", ondelete="CASCADE"), primary_key=True
    )
    connector: Mapped[str] = mapped_column(String(50), primary_key=True)
    checkpoint: Mapped[str] = mapped_column(Text)


class AuthorRow(Base):
    __tablename__ = "authors"
    __table_args__ = (UniqueConstraint("project_id", "channel", "external_id"),)

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(300))
    followers: Mapped[int | None] = mapped_column(BigInteger)
    verified: Mapped[bool] = mapped_column(Boolean)
    first_seen_at: Mapped[datetime] = mapped_column(Timestamp)
    last_seen_at: Mapped[datetime] = mapped_column(Timestamp)
    total_mentions: Mapped[int] = mapped_column(Integer)
    type_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    sentiment_trend: Mapped[float | None] = mapped_column(Float)
    recurrent: Mapped[bool] = mapped_column(Boolean)
    is_official: Mapped[bool] = mapped_column(Boolean)


class UsernameHistoryRow(Base):
    __tablename__ = "author_username_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    author_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("authors.id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str] = mapped_column(String(200))
    replaced_at: Mapped[datetime] = mapped_column(Timestamp)


class ThreadRow(Base):
    __tablename__ = "threads"
    __table_args__ = (UniqueConstraint("project_id", "channel", "root_external_id"),)

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(30))
    root_external_id: Mapped[str] = mapped_column(String(200))
    attended_by_entity: Mapped[bool] = mapped_column(Boolean)


class MentionRow(Base):
    __tablename__ = "mentions"
    __table_args__ = (
        UniqueConstraint("channel", "external_id"),
        Index("ix_mentions_project_published", "project_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[str] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    published_at: Mapped[datetime] = mapped_column(Timestamp)
    format: Mapped[str] = mapped_column(String(20))
    parent_external_id: Mapped[str | None] = mapped_column(String(200))
    author_external_id: Mapped[str] = mapped_column(String(200))
    author_username: Mapped[str] = mapped_column(String(200))
    author_display_name: Mapped[str | None] = mapped_column(String(300))
    author_followers: Mapped[int | None] = mapped_column(BigInteger)
    author_verified: Mapped[bool] = mapped_column(Boolean)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    replies: Mapped[int | None] = mapped_column(BigInteger)
    views: Mapped[int | None] = mapped_column(BigInteger)
    raw_text: Mapped[str | None] = mapped_column(Text)
    masked_text: Mapped[str | None] = mapped_column(Text)
    sensitive_types: Mapped[list[str]] = mapped_column(TextArray, server_default="{}")
    author_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("authors.id", ondelete="SET NULL"), index=True
    )
    thread_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("threads.id", ondelete="SET NULL"), index=True
    )


class MentionQueryRow(Base):
    __tablename__ = "mention_queries"

    mention_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("mentions.id", ondelete="CASCADE"), primary_key=True
    )
    query_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("listening_queries.id", ondelete="CASCADE"), primary_key=True
    )


class ClassificationRow(Base):
    __tablename__ = "classifications"

    mention_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("mentions.id", ondelete="CASCADE"), primary_key=True
    )
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    mention_type: Mapped[str] = mapped_column(String(40), index=True)
    taxonomy_version: Mapped[int] = mapped_column(Integer)
    channel_format: Mapped[str] = mapped_column(String(20))
    sentiment_score: Mapped[float] = mapped_column(Float)
    subcategory: Mapped[str | None] = mapped_column(String(100))
    needs_review: Mapped[bool] = mapped_column(Boolean)


class TaxonomyRow(Base):
    __tablename__ = "taxonomies"
    __table_args__ = (
        Index(
            "uq_taxonomies_project_version",
            "project_id",
            "version",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index(
            "uq_taxonomies_default_version",
            "version",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    categories: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class TopicRow(Base):
    __tablename__ = "topics"

    project_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    slug: Mapped[str] = mapped_column(String(120), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    mentions_count: Mapped[int] = mapped_column(Integer)


class IdentityResultRow(Base):
    __tablename__ = "identity_results"

    author_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(20))
    username: Mapped[str] = mapped_column(String(200))
    customer_ref: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column(Float)
    validated_at: Mapped[datetime] = mapped_column(Timestamp)
    attempts: Mapped[int] = mapped_column(Integer)


class JobRow(Base):
    __tablename__ = "pipeline_jobs"
    __table_args__ = (
        Index(
            "uq_pipeline_jobs_active",
            "stage",
            "subject_id",
            unique=True,
        ),
        Index(
            "ix_pipeline_jobs_ready",
            "stage",
            "available_at",
            postgresql_where=text("status = 'pendiente'"),
        ),
    )

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    stage: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(Timestamp)
    claimed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(Text)


class DeadLetterRow(Base):
    __tablename__ = "dead_letters"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True)
    stage: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(Timestamp, index=True)


class OutboxRow(Base):
    __tablename__ = "export_outbox"
    __table_args__ = (
        Index(
            "ix_export_outbox_pending",
            "next_attempt_at",
            postgresql_where=text("sent_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_name: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    span_id: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    next_attempt_at: Mapped[datetime] = mapped_column(Timestamp)
    attempts: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime | None] = mapped_column(Timestamp)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(Timestamp, index=True)
