"""Consulta mínima al microservicio de identidad y su respuesta."""

from __future__ import annotations

from dataclasses import dataclass

from social_listener.domain.identity.model import IdentityStatus


@dataclass(frozen=True, slots=True)
class IdentityLookup:
    channel: str
    external_author_id: str
    username: str


@dataclass(frozen=True, slots=True)
class IdentityLookupResult:
    """Solo uno de ``identificado``, ``no_identificado`` o ``ambiguo``."""

    status: IdentityStatus
    customer_ref: str | None = None
    confidence: float | None = None
