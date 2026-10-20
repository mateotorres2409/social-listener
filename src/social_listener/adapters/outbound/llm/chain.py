"""Cadena de modelos con respaldo entre proveedores y clasificador nulo."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from social_listener.adapters.outbound.resilience import CircuitBreaker
from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.ports.outbound.services import LLMClassifier

logger = logging.getLogger(__name__)

LinkObserver = Callable[[str, str, str, float], None]
"""(proveedor, modelo, resultado, segundos) con resultado ``ok`` | ``no_disponible`` |
``salida_invalida`` | ``circuito_abierto``."""


def _ignore(provider: str, model: str, outcome: str, seconds: float) -> None:
    return None


@dataclass
class ChainLink:
    provider: str
    model: str
    classifier: LLMClassifier
    timeout_s: float
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


class FallbackChainClassifier:
    """Recorre los eslabones en orden y devuelve la primera propuesta válida.

    Un timeout o error del proveedor cuenta para su circuit breaker; una salida
    inválida no (el proveedor respondió), pero igual se pasa al siguiente eslabón.
    """

    def __init__(self, links: Sequence[ChainLink], observer: LinkObserver = _ignore) -> None:
        self._links = list(links)
        self._observe = observer

    @property
    def links(self) -> list[ChainLink]:
        return list(self._links)

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        for link in self._links:
            if not link.breaker.allow():
                self._observe(link.provider, link.model, "circuito_abierto", 0.0)
                continue
            started = time.monotonic()
            try:
                proposal = await asyncio.wait_for(
                    link.classifier.classify(request), timeout=link.timeout_s
                )
            except (TimeoutError, ModelUnavailableError) as exc:
                link.breaker.record_failure()
                self._observe(
                    link.provider, link.model, "no_disponible", time.monotonic() - started
                )
                logger.info(
                    "eslabón %s/%s no disponible: %s", link.provider, link.model, type(exc).__name__
                )
                continue
            except InvalidModelOutputError:
                link.breaker.record_success()
                self._observe(
                    link.provider, link.model, "salida_invalida", time.monotonic() - started
                )
                continue
            link.breaker.record_success()
            self._observe(link.provider, link.model, "ok", time.monotonic() - started)
            return proposal
        raise ModelUnavailableError("ningún modelo de la cadena respondió")


class NullClassifier:
    """Para proyectos con los modelos de lenguaje desactivados: todo va al respaldo."""

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        raise ModelUnavailableError("modelos de lenguaje desactivados para el proyecto")
