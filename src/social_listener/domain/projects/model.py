"""Proyecto: unidad de aislamiento (multi-tenant) y su configuración de negocio."""

from __future__ import annotations

from dataclasses import dataclass, field

from social_listener.domain.classification.impact import ImpactThresholds
from social_listener.domain.mentions.model import Channel
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import ProjectId, require_unit_interval


@dataclass(frozen=True, slots=True)
class OfficialAccount:
    channel: Channel
    external_id: str


@dataclass(frozen=True, slots=True)
class RecurrenceRule:
    """Un autor es recurrente si supera ``threshold`` quejas/reclamos en ``window_days``."""

    threshold: int = 3
    window_days: int = 30

    def __post_init__(self) -> None:
        require(self.threshold >= 1, "el umbral de recurrencia debe ser >= 1")
        require(self.window_days >= 1, "la ventana de recurrencia debe ser >= 1 día")


@dataclass(frozen=True, slots=True)
class Project:
    id: ProjectId
    name: str
    retention_days: int = 365
    review_confidence_threshold: float = 0.6
    impact: ImpactThresholds = field(default_factory=ImpactThresholds)
    recurrence: RecurrenceRule = field(default_factory=RecurrenceRule)
    official_accounts: frozenset[OfficialAccount] = frozenset()

    def __post_init__(self) -> None:
        require(bool(self.name.strip()), "el proyecto requiere nombre")
        require(self.retention_days >= 1, "la retención debe ser de al menos 1 día")
        require_unit_interval(self.review_confidence_threshold, "review_confidence_threshold")

    def is_official(self, channel: Channel, external_id: str) -> bool:
        return OfficialAccount(channel, external_id) in self.official_accounts
