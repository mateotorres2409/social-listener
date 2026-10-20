"""Métricas de negocio: lista blanca de atributos y decoradores de etapas."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from social_listener.adapters.outbound.otel.metrics import (
    AttributeNotAllowedError,
    BusinessMetrics,
    MeteredExport,
    MeteredIdentity,
    MeteredProtection,
)
from social_listener.adapters.outbound.privacy.rule_detector import RuleBasedDetector
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.use_cases.authors.identity import ValidateIdentity
from social_listener.application.use_cases.classification.classify import ClassificationEvent
from social_listener.application.use_cases.export.assemble import EnrichedMentionAssembler
from social_listener.application.use_cases.export.export import ExportMention
from social_listener.application.use_cases.privacy.protect import ProtectMention
from social_listener.domain.mentions.model import IncomingMention
from social_listener.domain.privacy.model import MaskedText
from tests.contracts.persistence import P1, Q1, T0, incoming
from tests.fakes.memory import FixedClock
from tests.fakes.services import HmacPseudonymizer, PrefixIdentityService
from tests.unit.test_individualize import process, seeded


def points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict[str, Any], float]]:
    data = reader.get_metrics_data()
    found = []
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    for p in metric.data.data_points:
                        found.append((dict(p.attributes), getattr(p, "value", None) or p.count))
    return found


@pytest.fixture
def reader() -> InMemoryMetricReader:
    return InMemoryMetricReader()


@pytest.fixture
def metrics(reader: InMemoryMetricReader) -> BusinessMetrics:
    return BusinessMetrics(MeterProvider(metric_readers=[reader]).get_meter("prueba"))


def test_atributo_fuera_de_la_lista_blanca_se_rechaza(metrics: BusinessMetrics) -> None:
    with pytest.raises(AttributeNotAllowedError, match=r"social\.mention\.id"):
        metrics.mentions_processed.add(1, {"social.type": "queja", "social.mention.id": "m-1"})
    with pytest.raises(AttributeNotAllowedError):
        metrics.identity_results.add(1, {"social.author.pseudonym_id": "k1:abc"})


def test_tokens_y_eslabones_por_proveedor_y_modelo(
    metrics: BusinessMetrics, reader: InMemoryMetricReader
) -> None:
    metrics.on_classification(
        ClassificationEvent(
            "p-1", "modelo", None, "ollama", "qwen3:4b", {"input_tokens": 900, "output_tokens": 50}
        )
    )
    metrics.on_chain_link("anthropic", "claude-haiku-5-5", "no_disponible", 2.0)
    metrics.on_chain_link("ollama", "qwen3:4b", "ok", 0.8)

    tokens = {a["social.llm.token_type"]: v for a, v in points(reader, "social.llm.tokens")}
    assert tokens == {"input_tokens": 900, "output_tokens": 50}
    links = {
        (a["social.classifier.provider"], a["outcome"])
        for a, _ in points(reader, "social.llm.requests")
    }
    assert links == {("anthropic", "no_disponible"), ("ollama", "ok")}
    assert len(points(reader, "social.llm.duration")) == 2


async def test_decoradores_de_etapas(
    metrics: BusinessMetrics, reader: InMemoryMetricReader
) -> None:
    clock = FixedClock(T0 + timedelta(days=1))
    db = await seeded(clock)
    mention_id = await process(db, clock, "m-1", author=("a-1", "cliente_ana"))
    async with db() as uow:
        base = incoming("m-2")
        other, _ = await uow.mentions.add_or_get(
            P1, IncomingMention(base.data, "llamar al 3001234567 o ana@x.co"), Q1
        )
        await MeteredProtection(ProtectMention(RuleBasedDetector()), metrics).handle(
            uow, Job(id="j", stage=Stage.PROTECT, subject_id=other.id)
        )
        await uow.mentions.save_protection(mention_id, MaskedText("texto"))  # type: ignore[arg-type]
        await MeteredIdentity(
            ValidateIdentity(PrefixIdentityService(), HmacPseudonymizer(), clock), metrics
        ).handle(uow, Job(id="j", stage=Stage.IDENTITY, subject_id=mention_id))
        await MeteredExport(
            ExportMention(EnrichedMentionAssembler(HmacPseudonymizer()), clock), metrics
        ).handle(uow, Job(id="j", stage=Stage.EXPORT, subject_id=mention_id))
        await uow.commit()

    detections = {
        a["social.sensitive_data.type"] for a, _ in points(reader, "social.privacy.detections")
    }
    assert detections == {"telefono", "email"}
    assert points(reader, "social.identity.results") == [
        ({"social.identity.status": "identificado"}, 1)
    ]
    ((attributes, count),) = points(reader, "social.mentions.processed")
    assert count == 1
    assert set(attributes) == {
        "social.channel",
        "social.type",
        "social.sentiment.label",
        "social.category",
        "social.impact",
    }
