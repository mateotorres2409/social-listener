"""Resultado de la validación de identidad de un autor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import AuthorId, require_aware, require_unit_interval


class IdentityStatus(StrEnum):
    IDENTIFICADO = "identificado"
    NO_IDENTIFICADO = "no_identificado"
    AMBIGUO = "ambiguo"
    PENDIENTE = "pendiente"
    ERROR = "error"

    @property
    def is_definitive(self) -> bool:
        return self in {
            IdentityStatus.IDENTIFICADO,
            IdentityStatus.NO_IDENTIFICADO,
            IdentityStatus.AMBIGUO,
        }


@dataclass(frozen=True, slots=True)
class IdentityResult:
    """Solo guarda estado, referencia opaca, confianza y fecha (minimización)."""

    author_id: AuthorId
    status: IdentityStatus
    validated_at: datetime
    username: str
    customer_ref: str | None = None
    confidence: float | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        require_aware(self.validated_at, "validated_at")
        require(self.attempts >= 0, "attempts no puede ser negativo")
        if self.status is IdentityStatus.IDENTIFICADO:
            require(bool(self.customer_ref), "un autor identificado requiere referencia de cliente")
        else:
            require(self.customer_ref is None, "solo un autor identificado tiene referencia")
        if self.confidence is not None:
            require_unit_interval(self.confidence, "confidence")
