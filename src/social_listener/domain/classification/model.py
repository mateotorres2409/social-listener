"""Dimensiones de clasificación, taxonomía y temáticas."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum

from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import MentionId, ProjectId, require_unit_interval

INDETERMINADO = "indeterminado"


class Dimension(StrEnum):
    CANAL = "canal"
    IMPACTO = "impacto"
    TIPO = "tipo"
    SENTIMIENTO = "sentimiento"
    CATEGORIA = "categoria"
    TEMATICA = "tematica"


class MentionType(StrEnum):
    PETICION = "peticion"
    QUEJA = "queja"
    RECLAMO = "reclamo"
    SUGERENCIA = "sugerencia"
    FELICITACION = "felicitacion"
    CONSULTA = "consulta"
    ALERTA_FRAUDE = "alerta_fraude"
    RIESGO_REPUTACIONAL = "riesgo_reputacional"
    INFORMATIVA = "informativa"
    IRRELEVANTE = "irrelevante"


PQRS_TYPES = frozenset(
    {MentionType.PETICION, MentionType.QUEJA, MentionType.RECLAMO, MentionType.SUGERENCIA}
)


class SentimentLabel(StrEnum):
    POSITIVO = "positivo"
    NEUTRAL = "neutral"
    NEGATIVO = "negativo"
    MIXTO = "mixto"


class ImpactLevel(IntEnum):
    BAJO = 1
    MEDIO = 2
    ALTO = 3
    CRITICO = 4

    @property
    def label(self) -> str:
        return self.name.lower()


class ClassificationSource(StrEnum):
    REGLA = "regla"
    LLM = "llm"
    RESPALDO = "respaldo"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Sentiment:
    """Sentimiento con etiqueta y puntaje coherentes entre sí."""

    label: SentimentLabel
    score: float

    def __post_init__(self) -> None:
        require(-1.0 <= self.score <= 1.0, "el puntaje de sentimiento debe estar entre -1 y 1")
        match self.label:
            case SentimentLabel.POSITIVO:
                require(self.score > 0, "un sentimiento positivo requiere puntaje mayor que 0")
            case SentimentLabel.NEGATIVO:
                require(self.score < 0, "un sentimiento negativo requiere puntaje menor que 0")
            case SentimentLabel.NEUTRAL:
                require(abs(self.score) < 0.5, "un sentimiento neutral requiere |puntaje| < 0.5")
            case SentimentLabel.MIXTO:
                pass


@dataclass(frozen=True, slots=True)
class DimensionValue:
    """Valor de una dimensión con su trazabilidad."""

    value: str
    source: ClassificationSource
    confidence: float
    classifier_name: str
    classifier_version: str
    provider: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        require(bool(self.value.strip()), "el valor de la dimensión no puede estar vacío")
        require_unit_interval(self.confidence, "confidence")
        if self.source is ClassificationSource.LLM:
            require(
                bool(self.provider) and bool(self.model),
                "una dimensión clasificada por un modelo requiere proveedor y modelo",
            )

    @property
    def is_undetermined(self) -> bool:
        return self.value == INDETERMINADO


@dataclass(frozen=True, slots=True)
class MentionClassification:
    """Clasificación completa de una mención en las seis dimensiones."""

    mention_id: MentionId
    values: Mapping[Dimension, DimensionValue]
    taxonomy_version: int
    channel_format: str
    sentiment_score: float
    subcategory: str | None = None
    needs_review: bool = False

    def __post_init__(self) -> None:
        missing = set(Dimension) - set(self.values)
        require(not missing, f"faltan dimensiones: {sorted(d.value for d in missing)}")
        require(-1.0 <= self.sentiment_score <= 1.0, "puntaje de sentimiento fuera de rango")

    def value_of(self, dimension: Dimension) -> str:
        return self.values[dimension].value


@dataclass(frozen=True, slots=True)
class Subcategory:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class Category:
    code: str
    name: str
    subcategories: tuple[Subcategory, ...] = ()

    def has_subcategory(self, code: str) -> bool:
        return any(sub.code == code for sub in self.subcategories)


@dataclass(frozen=True, slots=True)
class Taxonomy:
    """Árbol de categorías versionado. ``project_id`` vacío es la taxonomía por defecto."""

    version: int
    categories: tuple[Category, ...]
    project_id: ProjectId | None = None

    def __post_init__(self) -> None:
        require(self.version >= 1, "la versión de la taxonomía empieza en 1")
        require(bool(self.categories), "la taxonomía requiere al menos una categoría")
        codes = [c.code for c in self.categories]
        require(len(codes) == len(set(codes)), "códigos de categoría duplicados")

    def category(self, code: str) -> Category | None:
        return next((c for c in self.categories if c.code == code), None)

    def contains(self, category: str, subcategory: str | None = None) -> bool:
        found = self.category(category)
        if found is None:
            return False
        return subcategory is None or found.has_subcategory(subcategory)

    def next_version(self, categories: tuple[Category, ...]) -> Taxonomy:
        return Taxonomy(version=self.version + 1, categories=categories, project_id=self.project_id)


@dataclass(frozen=True, slots=True)
class Topic:
    """Temática normalizada de un proyecto."""

    project_id: ProjectId
    slug: str
    label: str
    mentions_count: int = field(default=0)

    def __post_init__(self) -> None:
        require(bool(self.slug), "la temática requiere slug")
