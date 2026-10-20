"""Eventos que la plataforma exporta a través del outbox."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from social_listener.domain.classification.model import Dimension, DimensionValue
from social_listener.domain.identity.model import IdentityStatus


class EventName(StrEnum):
    MENTION_ENRICHED = "social.mention.enriched"
    MENTION_UPDATED = "social.mention.updated"
    AUTHOR_IDENTITY_RESOLVED = "social.author.identity.resolved"


@dataclass(frozen=True, slots=True)
class EnrichedMention:
    """Mención lista para exportar: sin texto original ni identificadores en claro."""

    mention_id: str
    project_id: str
    url: str
    language: str | None
    created_at: datetime
    channel: str
    channel_format: str
    masked_text: str
    dimensions: Mapping[Dimension, DimensionValue]
    sentiment_score: float
    subcategory: str | None
    taxonomy_version: int
    classifier_version: str
    author_pseudonym_id: str
    author_recurrent: bool
    thread_id: str | None
    identity_status: IdentityStatus
    listening_query_ids: Sequence[str]
    sensitive_data_types: Sequence[str] = ()
    customer_pseudonym: str | None = None
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class IdentityResolved:
    author_pseudonym_id: str
    project_id: str
    identity_status: IdentityStatus
    customer_pseudonym: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    name: EventName
    occurred_at: datetime
    mention: EnrichedMention | None = None
    identity: IdentityResolved | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)
