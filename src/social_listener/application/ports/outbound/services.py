"""Puertos de servicios externos: fuentes, privacidad, modelos, identidad y reloj."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
    ModelRoute,
)
from social_listener.application.dto.identity import IdentityLookup, IdentityLookupResult
from social_listener.application.dto.sources import ConnectorBatch
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.privacy.model import Detection


class Clock(Protocol):
    def now(self) -> datetime: ...


class SourceConnector(Protocol):
    """Conector de una fuente.

    Lanza ``RateLimitedError`` o ``TransientSourceError`` ante fallas
    reintentables y ``SourceError`` ante fallas definitivas.
    """

    @property
    def name(self) -> str: ...

    async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch: ...


class SensitiveDataDetector(Protocol):
    def detect(self, text: str) -> Sequence[Detection]: ...


class Pseudonymizer(Protocol):
    @property
    def key_id(self) -> str: ...

    def pseudonymize(self, namespace: str, value: str) -> str:
        """Seudónimo estable e irreversible sin la clave secreta."""
        ...


class LLMClassifier(Protocol):
    """Clasifica con un modelo de lenguaje, sin saber de qué proveedor se trata.

    Lanza ``ModelUnavailableError`` o ``InvalidModelOutputError``.
    """

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal: ...


class LLMClassifierProvider(Protocol):
    """Entrega el clasificador (cadena de modelos) configurado para un proyecto."""

    async def for_project(self, project_id: str) -> LLMClassifier: ...


class ModelChainValidator(Protocol):
    """Valida una cadena de modelos contra los proveedores disponibles.

    Lanza ``InvalidDomainValueError`` si un proveedor no existe o le falta configuración.
    """

    def validate(self, chain: Sequence[ModelRoute]) -> None: ...


class IdentityService(Protocol):
    """Lanza ``IdentityServiceUnavailableError`` si no responde a tiempo o falla."""

    async def lookup(self, query: IdentityLookup) -> IdentityLookupResult: ...
