"""Contrato neutro entre la clasificación y cualquier proveedor de modelos."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from social_listener.domain.classification.model import Dimension


@dataclass(frozen=True, slots=True)
class ClassificationRequest:
    """Solicitud idéntica para todos los proveedores.

    ``output_schema`` es un JSON Schema como estructura de datos neutra; cada
    adaptador lo traduce al mecanismo de salida estructurada de su API.
    """

    system: str
    user: str
    output_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProposedValue:
    value: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ClassificationProposal:
    """Propuesta de un modelo para las dimensiones que clasifica."""

    values: Mapping[Dimension, ProposedValue]
    provider: str
    model: str
    sentiment_score: float = 0.0
    subcategory: str | None = None
    topic_label: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """Un eslabón de la cadena de modelos de un proyecto."""

    provider: str
    model: str
    timeout_s: float = 20.0
    base_url: str | None = None
    credential_ref: str | None = None
    capabilities: frozenset[str] = frozenset()
    options: Mapping[str, str] = field(default_factory=dict)
    """Parámetros propios del proveedor, p. ej. ``region`` o ``project_id``. Nunca secretos."""
