"""Umbrales configurables para el cálculo del impacto."""

from __future__ import annotations

from dataclasses import dataclass

from social_listener.domain.shared.errors import require


@dataclass(frozen=True, slots=True)
class ImpactThresholds:
    """Umbrales de alcance e interacción que usa ``ImpactPolicy``."""

    influencer_followers: int = 10_000
    medium_followers: int = 1_000
    viral_interactions: int = 500
    medium_interactions: int = 50

    def __post_init__(self) -> None:
        require(
            0 < self.medium_followers <= self.influencer_followers,
            "medium_followers debe ser positivo y <= influencer_followers",
        )
        require(
            0 < self.medium_interactions <= self.viral_interactions,
            "medium_interactions debe ser positivo y <= viral_interactions",
        )
