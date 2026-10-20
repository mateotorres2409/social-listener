"""Etapa de clasificación híbrida: reglas + modelo de lenguaje + respaldo."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from social_listener.application.dto.classification import ClassificationProposal
from social_listener.application.dto.pipeline import Job
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.ports.inbound.stages import (
    ADVANCE,
    PermanentStageError,
    StageOutcome,
)
from social_listener.application.ports.outbound.services import LLMClassifierProvider
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.application.use_cases.classification.prompt import (
    ClassificationPromptBuilder,
    MentionContext,
)
from social_listener.domain.classification.catalog import (
    DEFAULT_TAXONOMY,
    SENTIMENT_VALUES,
    TYPE_VALUES,
)
from social_listener.domain.classification.fallback import (
    FALLBACK_CONFIDENCE,
    classify_with_fallback,
)
from social_listener.domain.classification.model import (
    INDETERMINADO,
    ClassificationSource,
    Dimension,
    DimensionValue,
    ImpactLevel,
    MentionClassification,
    MentionType,
    Sentiment,
    SentimentLabel,
    Taxonomy,
    Topic,
)
from social_listener.domain.classification.rules import (
    ImpactPolicy,
    channel_of,
    equivalent_topic,
    topic_slug,
)
from social_listener.domain.mentions.model import ContentFormat, Mention
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.types import MentionId

logger = logging.getLogger(__name__)

CLASSIFIER_NAME = "clasificador-hibrido"
CLASSIFIER_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ClassificationEvent:
    """Lo que la telemetría necesita saber de cada clasificación (sin datos personales)."""

    project_id: str
    outcome: str  # "modelo" | "respaldo"
    error: str | None
    provider: str | None
    model: str | None
    usage: Mapping[str, int]


ClassificationObserver = Callable[[ClassificationEvent], None]


def _ignore(_: ClassificationEvent) -> None:
    return None


class ClassifyMention:
    def __init__(
        self,
        models: LLMClassifierProvider,
        *,
        prompt_builder: ClassificationPromptBuilder | None = None,
        observer: ClassificationObserver = _ignore,
    ) -> None:
        self._models = models
        self._builder = prompt_builder or ClassificationPromptBuilder()
        self._observe = observer

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        mention = await uow.mentions.get(MentionId(job.subject_id))
        if mention is None or mention.text is None:
            raise PermanentStageError("la mención no existe o no pasó por protección")
        project = await uow.projects.get(mention.project_id)
        if project is None:
            raise PermanentStageError("el proyecto de la mención no existe")
        taxonomy = await uow.taxonomies.get_active(project.id) or DEFAULT_TAXONOMY
        topics = list(await uow.topics.most_frequent(project.id, self._builder.max_topics))

        proposal = await self._ask_model(mention, project, taxonomy, topics)
        classification, topic = self._decide(mention, project, taxonomy, topics, proposal)
        if topic is not None:
            await uow.topics.record_use(topic)
        await uow.classifications.save(classification)
        return ADVANCE

    async def _ask_model(
        self, mention: Mention, project: Project, taxonomy: Taxonomy, topics: list[Topic]
    ) -> ClassificationProposal | None:
        assert mention.text is not None  # noqa: S101 - verificado en handle
        context = MentionContext(
            masked_text=mention.text.value,
            channel=mention.data.channel.value,
            channel_format=mention.data.format.value,
            language=mention.data.language,
            is_reply=mention.data.format is ContentFormat.RESPUESTA,
        )
        request = self._builder.build(context, taxonomy, topics)
        try:
            classifier = await self._models.for_project(project.id)
            proposal = await classifier.classify(request)
        except (ModelUnavailableError, InvalidModelOutputError) as exc:
            self._observe(
                ClassificationEvent(project.id, "respaldo", type(exc).__name__, None, None, {})
            )
            logger.info("clasificación por respaldo: %s", type(exc).__name__)
            return None
        self._observe(
            ClassificationEvent(
                project.id, "modelo", None, proposal.provider, proposal.model, proposal.usage
            )
        )
        return proposal

    def _decide(
        self,
        mention: Mention,
        project: Project,
        taxonomy: Taxonomy,
        topics: list[Topic],
        proposal: ClassificationProposal | None,
    ) -> tuple[MentionClassification, Topic | None]:
        assert mention.text is not None  # noqa: S101 - verificado en handle
        fallback = classify_with_fallback(mention.text.value, taxonomy)
        invalid = False

        def from_rules(value: str) -> DimensionValue:
            return DimensionValue(
                value, ClassificationSource.REGLA, 1.0, CLASSIFIER_NAME, CLASSIFIER_VERSION
            )

        def from_fallback(value: str) -> DimensionValue:
            confidence = 0.0 if value == INDETERMINADO else FALLBACK_CONFIDENCE
            return DimensionValue(
                value,
                ClassificationSource.RESPALDO,
                confidence,
                CLASSIFIER_NAME,
                CLASSIFIER_VERSION,
            )

        def from_model(dimension: Dimension, allowed: frozenset[str] | None) -> DimensionValue:
            nonlocal invalid
            assert proposal is not None  # noqa: S101
            proposed = proposal.values.get(dimension)
            value, confidence = (proposed.value, proposed.confidence) if proposed else ("", 0.0)
            if not value or (allowed is not None and value not in allowed):
                invalid = True
                value, confidence = INDETERMINADO, 0.0
            return DimensionValue(
                value,
                ClassificationSource.LLM,
                confidence,
                CLASSIFIER_NAME,
                CLASSIFIER_VERSION,
                provider=proposal.provider,
                model=proposal.model,
            )

        channel, channel_format = channel_of(mention.data)
        values: dict[Dimension, DimensionValue] = {Dimension.CANAL: from_rules(channel)}
        topic: Topic | None = None
        subcategory: str | None = None

        if proposal is None:
            values[Dimension.TIPO] = from_fallback(fallback.mention_type.value)
            values[Dimension.SENTIMIENTO] = from_fallback(fallback.sentiment.label.value)
            values[Dimension.CATEGORIA] = from_fallback(fallback.category)
            values[Dimension.TEMATICA] = from_fallback(INDETERMINADO)
            sentiment_score = fallback.sentiment.score
        else:
            categories = frozenset(c.code for c in taxonomy.categories) | {INDETERMINADO}
            values[Dimension.TIPO] = from_model(Dimension.TIPO, TYPE_VALUES)
            values[Dimension.SENTIMIENTO] = from_model(Dimension.SENTIMIENTO, SENTIMENT_VALUES)
            values[Dimension.CATEGORIA] = from_model(Dimension.CATEGORIA, categories)
            sentiment_score = proposal.sentiment_score
            label = values[Dimension.SENTIMIENTO].value
            if label != INDETERMINADO:
                try:
                    Sentiment(SentimentLabel(label), sentiment_score)
                except ValueError:
                    invalid = True
                    values[Dimension.SENTIMIENTO] = from_model(Dimension.SENTIMIENTO, frozenset())
                    sentiment_score = 0.0
            category = values[Dimension.CATEGORIA].value
            if proposal.subcategory and taxonomy.contains(category, proposal.subcategory):
                subcategory = proposal.subcategory
            values[Dimension.TEMATICA], topic = self._topic(project, topics, proposal, from_model)

        values[Dimension.IMPACTO] = from_rules(
            self._impact(mention, project, values, sentiment_score).label
        )
        threshold = project.review_confidence_threshold
        needs_review = invalid or any(v.confidence < threshold for v in values.values())
        return (
            MentionClassification(
                mention_id=mention.id,
                values=values,
                taxonomy_version=taxonomy.version,
                channel_format=channel_format,
                sentiment_score=sentiment_score,
                subcategory=subcategory,
                needs_review=needs_review,
            ),
            topic,
        )

    @staticmethod
    def _topic(
        project: Project,
        topics: list[Topic],
        proposal: ClassificationProposal,
        from_model: Callable[[Dimension, frozenset[str] | None], DimensionValue],
    ) -> tuple[DimensionValue, Topic | None]:
        value = from_model(Dimension.TEMATICA, None)
        slug = topic_slug(proposal.topic_label or "")
        if value.is_undetermined or not slug:
            return value, None
        known = {t.slug: t for t in topics}
        slug = equivalent_topic(slug, known) or slug
        label = known[slug].label if slug in known else (proposal.topic_label or slug)
        return (
            DimensionValue(
                slug,
                value.source,
                value.confidence,
                value.classifier_name,
                value.classifier_version,
                provider=value.provider,
                model=value.model,
            ),
            Topic(project.id, slug, label),
        )

    @staticmethod
    def _impact(
        mention: Mention,
        project: Project,
        values: Mapping[Dimension, DimensionValue],
        sentiment_score: float,
    ) -> ImpactLevel:
        tipo = values[Dimension.TIPO].value
        sentiment = values[Dimension.SENTIMIENTO].value
        category = values[Dimension.CATEGORIA].value
        return ImpactPolicy(project.impact).evaluate(
            followers=mention.data.author.followers,
            interactions=mention.data.engagement.interactions,
            sentiment=SentimentLabel(sentiment)
            if sentiment != INDETERMINADO
            else SentimentLabel.NEUTRAL,
            mention_type=MentionType(tipo) if tipo != INDETERMINADO else None,
            category=None if category == INDETERMINADO else category,
        )
