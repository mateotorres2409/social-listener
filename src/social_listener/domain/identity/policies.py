"""Vigencia de los resultados de identidad."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from social_listener.domain.identity.model import IdentityResult, IdentityStatus

DEFAULT_TTL: Mapping[IdentityStatus, timedelta] = {
    IdentityStatus.IDENTIFICADO: timedelta(days=30),
    IdentityStatus.NO_IDENTIFICADO: timedelta(days=7),
    IdentityStatus.AMBIGUO: timedelta(days=7),
}


@dataclass(frozen=True, slots=True)
class IdentityTtlPolicy:
    """Cuánto se reutiliza un resultado antes de volver a consultar el servicio.

    ``pendiente`` nunca está vigente (se reintenta) y ``error`` siempre lo está: solo
    se revalida manualmente.
    """

    ttl: Mapping[IdentityStatus, timedelta] = field(default_factory=lambda: dict(DEFAULT_TTL))

    def is_fresh(self, result: IdentityResult | None, *, username: str, now: datetime) -> bool:
        if result is None or result.username != username:
            return False
        if result.status is IdentityStatus.ERROR:
            return True
        if result.status is IdentityStatus.PENDIENTE:
            return False
        return now < result.validated_at + self.ttl[result.status]
