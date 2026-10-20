"""Conversión entre objetos del dominio/aplicación y estructuras JSON persistibles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.export import (
    EnrichedMention,
    EventName,
    IdentityResolved,
    OutboxEvent,
)
from social_listener.domain.classification.impact import ImpactThresholds
from social_listener.domain.classification.model import (
    Category,
    ClassificationSource,
    Dimension,
    DimensionValue,
    Subcategory,
)
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.mentions.model import Channel
from social_listener.domain.projects.model import OfficialAccount, RecurrenceRule


def dimensions_to_json(values: Mapping[Dimension, DimensionValue]) -> dict[str, Any]:
    return {
        dimension.value: {
            "value": v.value,
            "source": v.source.value,
            "confidence": v.confidence,
            "classifier_name": v.classifier_name,
            "classifier_version": v.classifier_version,
            "provider": v.provider,
            "model": v.model,
        }
        for dimension, v in values.items()
    }


def dimensions_from_json(data: Mapping[str, Any]) -> dict[Dimension, DimensionValue]:
    return {
        Dimension(name): DimensionValue(
            value=v["value"],
            source=ClassificationSource(v["source"]),
            confidence=float(v["confidence"]),
            classifier_name=v["classifier_name"],
            classifier_version=v["classifier_version"],
            provider=v.get("provider"),
            model=v.get("model"),
        )
        for name, v in data.items()
    }


def categories_to_json(categories: tuple[Category, ...]) -> list[dict[str, Any]]:
    return [
        {
            "code": c.code,
            "name": c.name,
            "subcategories": [{"code": s.code, "name": s.name} for s in c.subcategories],
        }
        for c in categories
    ]


def categories_from_json(data: list[dict[str, Any]]) -> tuple[Category, ...]:
    return tuple(
        Category(
            code=c["code"],
            name=c["name"],
            subcategories=tuple(Subcategory(s["code"], s["name"]) for s in c["subcategories"]),
        )
        for c in data
    )


def impact_to_json(impact: ImpactThresholds) -> dict[str, int]:
    return {
        "influencer_followers": impact.influencer_followers,
        "medium_followers": impact.medium_followers,
        "viral_interactions": impact.viral_interactions,
        "medium_interactions": impact.medium_interactions,
    }


def impact_from_json(data: Mapping[str, Any]) -> ImpactThresholds:
    return ImpactThresholds(**{k: int(v) for k, v in data.items()})


def recurrence_to_json(rule: RecurrenceRule) -> dict[str, int]:
    return {"threshold": rule.threshold, "window_days": rule.window_days}


def recurrence_from_json(data: Mapping[str, Any]) -> RecurrenceRule:
    return RecurrenceRule(threshold=int(data["threshold"]), window_days=int(data["window_days"]))


def official_accounts_to_json(accounts: frozenset[OfficialAccount]) -> list[dict[str, str]]:
    return sorted(
        ({"channel": a.channel.value, "external_id": a.external_id} for a in accounts),
        key=lambda a: (a["channel"], a["external_id"]),
    )


def official_accounts_from_json(data: list[dict[str, str]]) -> frozenset[OfficialAccount]:
    return frozenset(OfficialAccount(Channel(a["channel"]), a["external_id"]) for a in data)


def model_route_to_json(route: ModelRoute) -> dict[str, Any]:
    return {
        "provider": route.provider,
        "model": route.model,
        "timeout_s": route.timeout_s,
        "base_url": route.base_url,
        "credential_ref": route.credential_ref,
        "capabilities": sorted(route.capabilities),
        "options": dict(route.options),
    }


def model_route_from_json(data: Mapping[str, Any]) -> ModelRoute:
    return ModelRoute(
        provider=data["provider"],
        model=data["model"],
        timeout_s=float(data["timeout_s"]),
        base_url=data.get("base_url"),
        credential_ref=data.get("credential_ref"),
        capabilities=frozenset(data.get("capabilities", ())),
        options={str(k): str(v) for k, v in (data.get("options") or {}).items()},
    )


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def outbox_event_to_json(event: OutboxEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": event.name.value,
        "occurred_at": _iso(event.occurred_at),
        "attributes": dict(event.attributes),
    }
    if event.mention is not None:
        m = event.mention
        payload["mention"] = {
            "mention_id": m.mention_id,
            "project_id": m.project_id,
            "url": m.url,
            "language": m.language,
            "created_at": _iso(m.created_at),
            "channel": m.channel,
            "channel_format": m.channel_format,
            "masked_text": m.masked_text,
            "dimensions": dimensions_to_json(m.dimensions),
            "sentiment_score": m.sentiment_score,
            "subcategory": m.subcategory,
            "taxonomy_version": m.taxonomy_version,
            "classifier_version": m.classifier_version,
            "author_pseudonym_id": m.author_pseudonym_id,
            "author_recurrent": m.author_recurrent,
            "thread_id": m.thread_id,
            "identity_status": m.identity_status.value,
            "listening_query_ids": list(m.listening_query_ids),
            "sensitive_data_types": list(m.sensitive_data_types),
            "customer_pseudonym": m.customer_pseudonym,
            "needs_review": m.needs_review,
        }
    if event.identity is not None:
        i = event.identity
        payload["identity"] = {
            "author_pseudonym_id": i.author_pseudonym_id,
            "project_id": i.project_id,
            "identity_status": i.identity_status.value,
            "customer_pseudonym": i.customer_pseudonym,
        }
    return payload


def outbox_event_from_json(data: Mapping[str, Any]) -> OutboxEvent:
    mention = None
    if (m := data.get("mention")) is not None:
        mention = EnrichedMention(
            mention_id=m["mention_id"],
            project_id=m["project_id"],
            url=m["url"],
            language=m["language"],
            created_at=datetime.fromisoformat(m["created_at"]),
            channel=m["channel"],
            channel_format=m["channel_format"],
            masked_text=m["masked_text"],
            dimensions=dimensions_from_json(m["dimensions"]),
            sentiment_score=float(m["sentiment_score"]),
            subcategory=m["subcategory"],
            taxonomy_version=int(m["taxonomy_version"]),
            classifier_version=m["classifier_version"],
            author_pseudonym_id=m["author_pseudonym_id"],
            author_recurrent=bool(m["author_recurrent"]),
            thread_id=m["thread_id"],
            identity_status=IdentityStatus(m["identity_status"]),
            listening_query_ids=tuple(m["listening_query_ids"]),
            sensitive_data_types=tuple(m["sensitive_data_types"]),
            customer_pseudonym=m["customer_pseudonym"],
            needs_review=bool(m["needs_review"]),
        )
    identity = None
    if (i := data.get("identity")) is not None:
        identity = IdentityResolved(
            author_pseudonym_id=i["author_pseudonym_id"],
            project_id=i["project_id"],
            identity_status=IdentityStatus(i["identity_status"]),
            customer_pseudonym=i["customer_pseudonym"],
        )
    return OutboxEvent(
        name=EventName(data["name"]),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        mention=mention,
        identity=identity,
        attributes=dict(data.get("attributes", {})),
    )
