"""Mapeo de eventos del outbox a LogRecords OpenTelemetry."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry._logs import SeverityNumber
from opentelemetry.util.types import AnyValue, AttributeValue

from social_listener.adapters.outbound.otel import schema as s
from social_listener.application.dto.export import EnrichedMention, EventName, OutboxEvent
from social_listener.domain.classification.model import ClassificationSource, Dimension
from social_listener.domain.identity.model import IdentityStatus


@dataclass(frozen=True, slots=True)
class MappedLog:
    event_name: str
    body: AnyValue
    attributes: dict[str, AttributeValue]
    severity_number: SeverityNumber
    severity_text: str
    timestamp_ns: int


def mention_attributes(m: EnrichedMention) -> dict[str, AttributeValue]:
    dims = m.dimensions
    attributes: dict[str, AttributeValue] = {
        s.SCHEMA: s.SCHEMA_VERSION,
        s.PROJECT_ID: m.project_id,
        s.MENTION_ID: m.mention_id,
        s.MENTION_URL: m.url,
        s.MENTION_LANG: m.language or "",
        s.MENTION_CREATED_AT: m.created_at.isoformat(),
        s.LISTENING_QUERY_IDS: list(m.listening_query_ids),
        s.SENSITIVE_DATA_TYPES: list(m.sensitive_data_types),
        s.REVIEW_REQUIRED: m.needs_review,
        s.CHANNEL: dims[Dimension.CANAL].value,
        s.CHANNEL_FORMAT: m.channel_format,
        s.IMPACT: dims[Dimension.IMPACTO].value,
        s.TYPE: dims[Dimension.TIPO].value,
        s.SENTIMENT_LABEL: dims[Dimension.SENTIMIENTO].value,
        s.SENTIMENT_SCORE: m.sentiment_score,
        s.CATEGORY: dims[Dimension.CATEGORIA].value,
        s.SUBCATEGORY: m.subcategory or "",
        s.TOPIC: dims[Dimension.TEMATICA].value,
        s.CLASSIFIER_VERSION: m.classifier_version,
        s.TAXONOMY_VERSION: m.taxonomy_version,
        s.AUTHOR_PSEUDONYM_ID: m.author_pseudonym_id,
        s.AUTHOR_RECURRENT: m.author_recurrent,
        s.THREAD_ID: m.thread_id or "",
        s.IDENTITY_STATUS: m.identity_status.value,
    }
    for dimension, prefix in s.DIMENSION_ATTRIBUTES.items():
        value = dims[Dimension(dimension)]
        attributes[f"{prefix}.source"] = value.source.value
        attributes[f"{prefix}.confidence"] = value.confidence
    model_value = next(
        (v for v in dims.values() if v.source is ClassificationSource.LLM and v.provider), None
    )
    if model_value is not None:
        attributes[s.CLASSIFIER_PROVIDER] = model_value.provider or ""
        attributes[s.CLASSIFIER_MODEL] = model_value.model or ""
    if m.identity_status is IdentityStatus.IDENTIFICADO and m.customer_pseudonym:
        attributes[s.IDENTITY_CUSTOMER_PSEUDONYM] = m.customer_pseudonym
    return attributes


def to_log(event: OutboxEvent) -> MappedLog:
    timestamp_ns = int(event.occurred_at.timestamp() * 1_000_000_000)
    if event.mention is not None:
        attributes = mention_attributes(event.mention)
        for key, value in event.attributes.items():
            if isinstance(value, str | bool | int | float):
                attributes[key] = value
            elif isinstance(value, list):
                attributes[key] = [str(v) for v in value]
        severity, text = s.SEVERITY_BY_IMPACT[event.mention.dimensions[Dimension.IMPACTO].value]
        return MappedLog(
            event_name=event.name.value,
            body=event.mention.masked_text,
            attributes=attributes,
            severity_number=severity,
            severity_text=text,
            timestamp_ns=timestamp_ns,
        )
    if event.name is EventName.AUTHOR_IDENTITY_RESOLVED and event.identity is not None:
        identity = event.identity
        attributes = {
            s.SCHEMA: s.SCHEMA_VERSION,
            s.PROJECT_ID: identity.project_id,
            s.AUTHOR_PSEUDONYM_ID: identity.author_pseudonym_id,
            s.IDENTITY_STATUS: identity.identity_status.value,
        }
        if identity.identity_status is IdentityStatus.IDENTIFICADO and identity.customer_pseudonym:
            attributes[s.IDENTITY_CUSTOMER_PSEUDONYM] = identity.customer_pseudonym
        return MappedLog(
            event_name=event.name.value,
            body=f"identidad del autor resuelta: {identity.identity_status.value}",
            attributes=attributes,
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            timestamp_ns=timestamp_ns,
        )
    raise ValueError(f"evento sin contenido exportable: {event.name.value}")
