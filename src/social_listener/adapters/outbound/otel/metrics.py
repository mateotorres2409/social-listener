"""Métricas de negocio con lista blanca de atributos (baja cardinalidad).

Las métricas nunca llevan ids de mención, autor o cliente ni texto: cada instrumento
declara qué atributos admite y rechaza cualquier otro. Se emiten desde decoradores de
los casos de uso (que ``bootstrap`` aplica) y observadores, no desde el núcleo.
"""

from __future__ import annotations

from collections.abc import Mapping

from opentelemetry.metrics import Counter, Histogram, Meter

from social_listener.application.dto.pipeline import Job
from social_listener.application.ports.inbound.stages import StageHandler, StageOutcome
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.application.use_cases.classification.classify import ClassificationEvent
from social_listener.domain.classification.model import Dimension
from social_listener.domain.shared.types import MentionId

AttributeValue = str | bool | int


class AttributeNotAllowedError(ValueError):
    """Se intentó registrar una métrica con un atributo fuera de su lista blanca."""


class GuardedCounter:
    def __init__(self, counter: Counter, allowed: frozenset[str]) -> None:
        self._counter = counter
        self.allowed = allowed

    def add(self, amount: int, attributes: Mapping[str, AttributeValue]) -> None:
        extra = set(attributes) - self.allowed
        if extra:
            raise AttributeNotAllowedError(f"atributos no permitidos: {sorted(extra)}")
        self._counter.add(amount, dict(attributes))


class GuardedHistogram:
    def __init__(self, histogram: Histogram, allowed: frozenset[str]) -> None:
        self._histogram = histogram
        self.allowed = allowed

    def record(self, value: float, attributes: Mapping[str, AttributeValue]) -> None:
        extra = set(attributes) - self.allowed
        if extra:
            raise AttributeNotAllowedError(f"atributos no permitidos: {sorted(extra)}")
        self._histogram.record(value, dict(attributes))


MENTION_DIMENSIONS = frozenset(
    {"social.channel", "social.type", "social.sentiment.label", "social.category", "social.impact"}
)
MODEL = frozenset({"social.project.id", "social.classifier.provider", "social.classifier.model"})


class BusinessMetrics:
    def __init__(self, meter: Meter) -> None:
        self.mentions_processed = GuardedCounter(
            meter.create_counter("social.mentions.processed", description="Menciones exportadas"),
            MENTION_DIMENSIONS,
        )
        self.identity_results = GuardedCounter(
            meter.create_counter("social.identity.results", description="Resultados de identidad"),
            frozenset({"social.identity.status"}),
        )
        self.sensitive_detections = GuardedCounter(
            meter.create_counter(
                "social.privacy.detections", description="Datos sensibles detectados"
            ),
            frozenset({"social.sensitive_data.type"}),
        )
        self.llm_tokens = GuardedCounter(
            meter.create_counter("social.llm.tokens", unit="{token}"),
            MODEL | {"social.llm.token_type"},
        )
        self.llm_requests = GuardedCounter(
            meter.create_counter("social.llm.requests", description="Intentos por eslabón"),
            frozenset({"social.classifier.provider", "social.classifier.model", "outcome"}),
        )
        self.llm_latency = GuardedHistogram(
            meter.create_histogram("social.llm.duration", unit="s"),
            frozenset({"social.classifier.provider", "social.classifier.model", "outcome"}),
        )
        self.classification_outcomes = GuardedCounter(
            meter.create_counter(
                "social.classification.outcomes", description="Clasificación por modelo o respaldo"
            ),
            frozenset({"social.project.id", "outcome", "error.type"}),
        )

    # Observadores ---------------------------------------------------------------

    def on_chain_link(self, provider: str, model: str, outcome: str, seconds: float) -> None:
        attributes = {
            "social.classifier.provider": provider,
            "social.classifier.model": model,
            "outcome": outcome,
        }
        self.llm_requests.add(1, attributes)
        if outcome != "circuito_abierto":
            self.llm_latency.record(seconds, attributes)

    def on_classification(self, event: ClassificationEvent) -> None:
        self.classification_outcomes.add(
            1,
            {
                "social.project.id": event.project_id,
                "outcome": event.outcome,
                "error.type": event.error or "",
            },
        )
        if event.provider and event.model:
            for token_type, amount in event.usage.items():
                if amount:
                    self.llm_tokens.add(
                        amount,
                        {
                            "social.project.id": event.project_id,
                            "social.classifier.provider": event.provider,
                            "social.classifier.model": event.model,
                            "social.llm.token_type": token_type,
                        },
                    )


class _MeteredStage:
    def __init__(self, inner: StageHandler, metrics: BusinessMetrics) -> None:
        self._inner = inner
        self._metrics = metrics

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        outcome = await self._inner.handle(uow, job)
        await self._record(uow, MentionId(job.subject_id))
        return outcome

    async def _record(self, uow: UnitOfWork, mention_id: MentionId) -> None:
        raise NotImplementedError


class MeteredProtection(_MeteredStage):
    async def _record(self, uow: UnitOfWork, mention_id: MentionId) -> None:
        mention = await uow.mentions.get(mention_id)
        if mention is None or mention.text is None:
            return
        for kind in mention.text.detected:
            self._metrics.sensitive_detections.add(1, {"social.sensitive_data.type": kind.value})


class MeteredIdentity(_MeteredStage):
    async def _record(self, uow: UnitOfWork, mention_id: MentionId) -> None:
        author_id = await uow.mentions.author_of(mention_id)
        result = await uow.identity_results.get(author_id) if author_id else None
        status = result.status.value if result else "pendiente"
        self._metrics.identity_results.add(1, {"social.identity.status": status})


class MeteredExport(_MeteredStage):
    async def _record(self, uow: UnitOfWork, mention_id: MentionId) -> None:
        classification = await uow.classifications.get(mention_id)
        if classification is None:
            return
        self._metrics.mentions_processed.add(
            1,
            {
                "social.channel": classification.value_of(Dimension.CANAL),
                "social.type": classification.value_of(Dimension.TIPO),
                "social.sentiment.label": classification.value_of(Dimension.SENTIMIENTO),
                "social.category": classification.value_of(Dimension.CATEGORIA),
                "social.impact": classification.value_of(Dimension.IMPACTO),
            },
        )
