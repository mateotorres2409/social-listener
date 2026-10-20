"""Taxonomía versionada y corrección manual de clasificaciones."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.export import EventName, OutboxEvent
from social_listener.application.errors import NotFoundError
from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.application.ports.outbound.services import Clock, ModelChainValidator
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.application.use_cases.classification.classify import (
    CLASSIFIER_NAME,
    CLASSIFIER_VERSION,
)
from social_listener.application.use_cases.export.assemble import EnrichedMentionAssembler
from social_listener.domain.classification.catalog import (
    DEFAULT_TAXONOMY,
    IMPACT_VALUES,
    SENTIMENT_VALUES,
    TYPE_VALUES,
)
from social_listener.domain.classification.model import (
    Category,
    ClassificationSource,
    Dimension,
    DimensionValue,
    MentionClassification,
    Sentiment,
    SentimentLabel,
    Taxonomy,
    Topic,
)
from social_listener.domain.classification.rules import topic_slug
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import MentionId, ProjectId

DEFAULT_SCORES = {
    SentimentLabel.POSITIVO: 0.6,
    SentimentLabel.NEUTRAL: 0.0,
    SentimentLabel.NEGATIVO: -0.6,
    SentimentLabel.MIXTO: 0.0,
}


class ManageTaxonomy:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def active(self, project_id: ProjectId) -> Taxonomy:
        async with self._uow_factory() as uow:
            return await uow.taxonomies.get_active(project_id) or DEFAULT_TAXONOMY

    async def publish(self, project_id: ProjectId, categories: tuple[Category, ...]) -> Taxonomy:
        """Publica una versión nueva. Lo ya clasificado conserva su versión."""
        async with self._uow_factory() as uow:
            if await uow.projects.get(project_id) is None:
                raise NotFoundError(f"proyecto {project_id} no existe")
            current = await uow.taxonomies.get_active(project_id) or DEFAULT_TAXONOMY
            taxonomy = Taxonomy(
                version=current.version + 1, categories=categories, project_id=project_id
            )
            await uow.taxonomies.publish(taxonomy)
            await uow.commit()
        return taxonomy


class CorrectClassification:
    """Un analista corrige dimensiones: origen ``manual``, confianza 1 y evento de actualización."""

    CORRECTABLE = frozenset(
        {
            Dimension.TIPO,
            Dimension.SENTIMIENTO,
            Dimension.CATEGORIA,
            Dimension.TEMATICA,
            Dimension.IMPACTO,
        }
    )

    def __init__(
        self, uow_factory: UnitOfWorkFactory, assembler: EnrichedMentionAssembler, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._assembler = assembler
        self._clock = clock

    async def execute(
        self,
        mention_id: MentionId,
        corrections: Mapping[Dimension, str],
        *,
        actor: str,
        sentiment_score: float | None = None,
        subcategory: str | None = None,
    ) -> MentionClassification:
        require(bool(corrections), "indica al menos una dimensión a corregir")
        not_allowed = set(corrections) - self.CORRECTABLE
        require(not not_allowed, f"no se puede corregir: {sorted(d.value for d in not_allowed)}")
        async with self._uow_factory() as uow:
            current = await uow.classifications.get(mention_id)
            mention = await uow.mentions.get(mention_id)
            if current is None or mention is None:
                raise NotFoundError(f"la mención {mention_id} no está clasificada")
            project = await uow.projects.get(mention.project_id)
            if project is None:
                raise NotFoundError("el proyecto de la mención no existe")
            taxonomy = await uow.taxonomies.get_active(project.id) or DEFAULT_TAXONOMY

            values = dict(current.values)
            score = current.sentiment_score
            sub = current.subcategory
            for dimension, raw in corrections.items():
                value = raw.strip()
                self._validate(dimension, value, taxonomy)
                if dimension is Dimension.TEMATICA:
                    value = topic_slug(value)
                    await uow.topics.record_use(Topic(project.id, value, raw.strip()))
                if dimension is Dimension.SENTIMIENTO:
                    label = SentimentLabel(value)
                    score = DEFAULT_SCORES[label] if sentiment_score is None else sentiment_score
                    Sentiment(label, score)  # valida coherencia
                if dimension is Dimension.CATEGORIA:
                    sub = None
                values[dimension] = DimensionValue(
                    value, ClassificationSource.MANUAL, 1.0, CLASSIFIER_NAME, CLASSIFIER_VERSION
                )
            if subcategory is not None:
                category = values[Dimension.CATEGORIA].value
                require(
                    taxonomy.contains(category, subcategory),
                    f"la subcategoría {subcategory} no pertenece a {category}",
                )
                sub = subcategory

            threshold = project.review_confidence_threshold
            updated = replace(
                current,
                values=values,
                sentiment_score=score,
                subcategory=sub,
                needs_review=any(
                    v.confidence < threshold and v.source is not ClassificationSource.MANUAL
                    for v in values.values()
                ),
            )
            await uow.classifications.save(updated)
            enriched = await self._assembler.assemble(uow, mention_id)
            now = self._clock.now()
            await uow.outbox.add(
                OutboxEvent(
                    name=EventName.MENTION_UPDATED,
                    occurred_at=now,
                    mention=enriched,
                    attributes={
                        "social.update.reason": "correccion_manual",
                        "social.update.dimensions": sorted(d.value for d in corrections),
                    },
                )
            )
            await uow.audit.record(
                AuditEntry(
                    action="correccion " + ",".join(sorted(d.value for d in corrections)),
                    subject=f"mencion:{mention_id}",
                    occurred_at=now,
                    actor=actor,
                )
            )
            await uow.commit()
        return updated

    @staticmethod
    def _validate(dimension: Dimension, value: str, taxonomy: Taxonomy) -> None:
        allowed: dict[Dimension, frozenset[str]] = {
            Dimension.TIPO: TYPE_VALUES,
            Dimension.SENTIMIENTO: SENTIMENT_VALUES,
            Dimension.IMPACTO: IMPACT_VALUES,
            Dimension.CATEGORIA: frozenset(c.code for c in taxonomy.categories),
        }
        if dimension in allowed:
            require(
                value in allowed[dimension],
                f"valor inválido para {dimension.value}: {value!r}",
            )
        else:
            require(bool(topic_slug(value)), "la temática no puede estar vacía")


class ConfigureModelChain:
    """Cadena ordenada de modelos de un proyecto. Vacía = modelos desactivados."""

    def __init__(self, uow_factory: UnitOfWorkFactory, validator: ModelChainValidator) -> None:
        self._uow_factory = uow_factory
        self._validator = validator

    async def get(self, project_id: ProjectId) -> Sequence[ModelRoute]:
        async with self._uow_factory() as uow:
            return await uow.model_chains.get(project_id)

    async def configure(
        self, project_id: ProjectId, chain: Sequence[ModelRoute]
    ) -> Sequence[ModelRoute]:
        self._validator.validate(chain)  # si falla, la cadena anterior sigue vigente
        async with self._uow_factory() as uow:
            if await uow.projects.get(project_id) is None:
                raise NotFoundError(f"proyecto {project_id} no existe")
            await uow.model_chains.save(project_id, chain)
            await uow.commit()
        return chain
