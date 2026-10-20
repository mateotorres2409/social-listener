"""Reglas deterministas: canal, impacto y temáticas."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable

from social_listener.domain.classification.impact import ImpactThresholds
from social_listener.domain.classification.model import (
    ImpactLevel,
    MentionType,
    SentimentLabel,
)
from social_listener.domain.mentions.model import MentionData

RISK_TYPES = frozenset({MentionType.ALERTA_FRAUDE, MentionType.RIESGO_REPUTACIONAL})
COMPLAINT_TYPES = frozenset({MentionType.QUEJA, MentionType.RECLAMO, MentionType.PETICION})
RISK_CATEGORIES = frozenset({"fraude_seguridad", "reputacion_corporativa"})


def channel_of(data: MentionData) -> tuple[str, str]:
    """Canal y formato salen directamente de la mención canónica."""
    return data.channel.value, data.format.value


class ImpactPolicy:
    """Calcula el impacto con umbrales configurables por proyecto.

    1. El alcance (seguidores o interacciones) fija el nivel base.
    2. Lo negativo sube un nivel si el alcance es bajo; una queja negativa de alto
       alcance y viral es crítica.
    3. Alertas de fraude y riesgos reputacionales tienen piso ``alto`` (``critico``
       si además el alcance es alto); lo mismo una mención negativa de una categoría
       de riesgo.
    4. Lo positivo o neutral sin riesgo no supera ``medio``.
    """

    def __init__(self, thresholds: ImpactThresholds) -> None:
        self._t = thresholds

    def reach(self, followers: int | None, interactions: int) -> ImpactLevel:
        followers = followers or 0
        if followers >= self._t.influencer_followers or interactions >= self._t.viral_interactions:
            return ImpactLevel.ALTO
        if followers >= self._t.medium_followers or interactions >= self._t.medium_interactions:
            return ImpactLevel.MEDIO
        return ImpactLevel.BAJO

    def evaluate(
        self,
        *,
        followers: int | None,
        interactions: int,
        sentiment: SentimentLabel,
        mention_type: MentionType | None,
        category: str | None,
    ) -> ImpactLevel:
        reach = self.reach(followers, interactions)
        level = reach
        negative = sentiment in {SentimentLabel.NEGATIVO, SentimentLabel.MIXTO}
        if negative:
            if reach is ImpactLevel.BAJO:
                level = ImpactLevel.MEDIO
            viral = interactions >= self._t.viral_interactions
            if reach is ImpactLevel.ALTO and viral and mention_type in COMPLAINT_TYPES:
                level = ImpactLevel.CRITICO
        is_risk = mention_type in RISK_TYPES or (negative and category in RISK_CATEGORIES)
        if is_risk:
            floor = ImpactLevel.CRITICO if reach is ImpactLevel.ALTO else ImpactLevel.ALTO
            level = max(level, floor)
        if not negative and not is_risk:
            level = min(level, ImpactLevel.MEDIO)
        return level


# Temáticas ---------------------------------------------------------------------

_MAX_TOPIC_WORDS = 6
_STOPWORDS = frozenset(
    [
        "a",
        "al",
        "con",
        "de",
        "del",
        "el",
        "en",
        "la",
        "las",
        "lo",
        "los",
        "mi",
        "para",
        "por",
        "que",
        "se",
        "su",
        "un",
        "una",
        "y",
        "o",
    ]
)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def topic_slug(label: str) -> str:
    """``"Caída de la App"`` → ``"caida-app"``."""
    words = re.findall(r"[a-z0-9]+", strip_accents(label).lower())
    meaningful = [w for w in words if w not in _STOPWORDS] or words
    return "-".join(meaningful[:_MAX_TOPIC_WORDS])


def _similarity(a: str, b: str) -> float:
    tokens_a, tokens_b = set(a.split("-")), set(b.split("-"))
    union = tokens_a | tokens_b
    jaccard = len(tokens_a & tokens_b) / len(union) if union else 0.0
    smaller = min(len(tokens_a), len(tokens_b))
    # Una temática de 2+ palabras contenida en otra se considera la misma ("caida-app").
    overlap = len(tokens_a & tokens_b) / smaller if smaller >= 2 else 0.0
    return max(jaccard, overlap, difflib.SequenceMatcher(None, a, b).ratio())


def equivalent_topic(slug: str, existing: Iterable[str], threshold: float = 0.8) -> str | None:
    """Devuelve la temática existente equivalente a ``slug``, si la hay."""
    best: tuple[float, str] | None = None
    for candidate in existing:
        if candidate == slug:
            return candidate
        score = _similarity(slug, candidate)
        if score >= threshold and (best is None or score > best[0]):
            best = (score, candidate)
    return best[1] if best else None
