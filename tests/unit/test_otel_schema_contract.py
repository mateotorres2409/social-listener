"""Prueba de contrato del esquema de atributos ``social.*``."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from social_listener.adapters.outbound.otel import schema as s
from social_listener.adapters.outbound.otel.mapping import mention_attributes, to_log
from social_listener.application.dto.export import (
    EnrichedMention,
    EventName,
    IdentityResolved,
    OutboxEvent,
)
from social_listener.domain.classification.model import (
    ClassificationSource,
    Dimension,
    DimensionValue,
)
from social_listener.domain.identity.model import IdentityStatus

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def dv(value: str, source: ClassificationSource = ClassificationSource.REGLA) -> DimensionValue:
    if source is ClassificationSource.LLM:
        return DimensionValue(value, source, 0.9, "c", "1", provider="ollama", model="qwen3:4b")
    return DimensionValue(
        value, source, 1.0 if source is ClassificationSource.REGLA else 0.4, "c", "1"
    )


def enriched(**kw: object) -> EnrichedMention:
    base: dict[str, object] = {
        "mention_id": "m-1",
        "project_id": "p-1",
        "url": "https://x.com/s/1",
        "language": "es",
        "created_at": NOW,
        "channel": "x",
        "channel_format": "publicacion",
        "masked_text": "me cobraron en la tarjeta [TARJETA]",
        "dimensions": {
            Dimension.CANAL: dv("x"),
            Dimension.IMPACTO: dv("alto"),
            Dimension.TIPO: dv("reclamo", ClassificationSource.LLM),
            Dimension.SENTIMIENTO: dv("negativo", ClassificationSource.LLM),
            Dimension.CATEGORIA: dv("tarifas_costos", ClassificationSource.RESPALDO),
            Dimension.TEMATICA: dv("cobro-doble", ClassificationSource.LLM),
        },
        "sentiment_score": -0.7,
        "subcategory": None,
        "taxonomy_version": 1,
        "classifier_version": "1.0.0",
        "author_pseudonym_id": "k1:abc",
        "author_recurrent": False,
        "thread_id": None,
        "identity_status": IdentityStatus.IDENTIFICADO,
        "listening_query_ids": ["q-1"],
        "sensitive_data_types": ["tarjeta"],
        "customer_pseudonym": "k1:def",
    }
    base.update(kw)
    return EnrichedMention(**base)  # type: ignore[arg-type]


def test_atributos_requeridos_presentes() -> None:
    attributes = mention_attributes(enriched())
    missing = [a for a in s.REQUIRED_MENTION_ATTRIBUTES if a not in attributes]
    assert missing == []
    assert attributes[s.SCHEMA] == s.SCHEMA_VERSION


def test_ningun_atributo_prohibido() -> None:
    attributes = mention_attributes(enriched())
    assert not s.FORBIDDEN_ATTRIBUTES & set(attributes)
    assert all(key.startswith("social.") for key in attributes)
    assert all(value is not None for value in attributes.values())


def test_trazabilidad_por_dimension_y_modelo() -> None:
    attributes = mention_attributes(enriched())
    assert attributes["social.category.source"] == "respaldo"
    assert attributes["social.type.source"] == "llm"
    assert attributes["social.type.confidence"] == 0.9
    assert attributes[s.CLASSIFIER_PROVIDER] == "ollama"
    assert attributes[s.CLASSIFIER_MODEL] == "qwen3:4b"


def test_sin_modelo_no_hay_atributos_de_proveedor() -> None:
    dims = {d: dv("indeterminado" if d is not Dimension.CANAL else "x") for d in Dimension}
    dims[Dimension.IMPACTO] = dv("bajo")
    attributes = mention_attributes(enriched(dimensions=dims))
    assert s.CLASSIFIER_PROVIDER not in attributes


@pytest.mark.parametrize("status", [IdentityStatus.NO_IDENTIFICADO, IdentityStatus.PENDIENTE])
def test_seudonimo_de_cliente_solo_si_identificado(status: IdentityStatus) -> None:
    attributes = mention_attributes(enriched(identity_status=status))
    assert s.IDENTITY_CUSTOMER_PSEUDONYM not in attributes


@pytest.mark.parametrize(
    ("impact", "severity"),
    [("bajo", "INFO"), ("medio", "INFO2"), ("alto", "WARN"), ("critico", "ERROR")],
)
def test_severidad_derivada_del_impacto(impact: str, severity: str) -> None:
    m = enriched()
    dims = {**m.dimensions, Dimension.IMPACTO: dv(impact)}
    log = to_log(OutboxEvent(EventName.MENTION_ENRICHED, NOW, mention=replace(m, dimensions=dims)))
    assert log.severity_text == severity
    assert log.body == m.masked_text
    assert log.event_name == "social.mention.enriched"


def test_evento_de_identidad_resuelta() -> None:
    log = to_log(
        OutboxEvent(
            EventName.AUTHOR_IDENTITY_RESOLVED,
            NOW,
            identity=IdentityResolved("k1:abc", "p-1", IdentityStatus.IDENTIFICADO, "k1:def"),
        )
    )
    assert log.attributes[s.AUTHOR_PSEUDONYM_ID] == "k1:abc"
    assert log.attributes[s.IDENTITY_CUSTOMER_PSEUDONYM] == "k1:def"
    assert not s.FORBIDDEN_ATTRIBUTES & set(log.attributes)
