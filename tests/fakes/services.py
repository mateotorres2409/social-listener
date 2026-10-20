"""Fakes de los puertos de servicios externos."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.dto.identity import IdentityLookup, IdentityLookupResult
from social_listener.application.dto.sources import ConnectorBatch, RawMention
from social_listener.application.errors import (
    IdentityServiceUnavailableError,
    ModelUnavailableError,
)
from social_listener.application.ports.outbound.services import LLMClassifier
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.privacy.model import Detection


class ScriptedConnector:
    """Conector que entrega lotes de una lista; el checkpoint es el índice del lote.

    Un elemento del guion puede ser una excepción, que se lanza una vez.
    """

    def __init__(
        self, script: Sequence[Sequence[RawMention] | Exception], name: str = "fake"
    ) -> None:
        self._script = list(script)
        self._name = name
        self.calls: list[str | None] = []

    @property
    def name(self) -> str:
        return self._name

    async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch:
        self.calls.append(checkpoint)
        index = int(checkpoint or 0)
        while index < len(self._script) and isinstance(self._script[index], Exception):
            error = self._script[index]
            self._script[index] = []  # la próxima vez el lote sale vacío pero avanza
            assert isinstance(error, Exception)
            raise error
        if index >= len(self._script):
            return ConnectorBatch(mentions=[], next_checkpoint=checkpoint, has_more=False)
        batch = self._script[index]
        assert not isinstance(batch, Exception)
        return ConnectorBatch(
            mentions=list(batch),
            next_checkpoint=str(index + 1),
            has_more=index + 1 < len(self._script),
        )


class NoDetections:
    def detect(self, text: str) -> Sequence[Detection]:
        return []


class HmacPseudonymizer:
    """Fake determinista con la misma semántica que el adaptador real."""

    def __init__(self, key: bytes = b"clave-de-prueba", key_id: str = "k-test") -> None:
        self._key = key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def pseudonymize(self, namespace: str, value: str) -> str:
        digest = hmac.new(self._key, f"{namespace}:{value}".encode(), hashlib.sha256)
        return f"{self._key_id}:{digest.hexdigest()[:32]}"


@dataclass
class ScriptedLLMClassifier:
    """Responde con ``responder`` y registra cada solicitud."""

    responder: Callable[[ClassificationRequest], ClassificationProposal] | None = None
    error: Exception | None = None
    requests: list[ClassificationRequest] = field(default_factory=list)

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.responder is None:
            raise ModelUnavailableError("sin modelo configurado")
        return self.responder(request)


class StaticLLMProvider:
    def __init__(self, classifier: LLMClassifier) -> None:
        self.classifier = classifier

    async def for_project(self, project_id: str) -> LLMClassifier:
        return self.classifier


@dataclass
class PrefixIdentityService:
    """Decide el estado por prefijo de username, como el stub del microservicio."""

    unavailable: bool = False
    lookups: list[IdentityLookup] = field(default_factory=list)

    async def lookup(self, query: IdentityLookup) -> IdentityLookupResult:
        self.lookups.append(query)
        if self.unavailable or query.username.startswith("lento_"):
            raise IdentityServiceUnavailableError("sin respuesta")
        if query.username.startswith("cliente_"):
            return IdentityLookupResult(
                status=IdentityStatus.IDENTIFICADO,
                customer_ref=f"ref-{query.username}",
                confidence=0.95,
            )
        if query.username.startswith("ambiguo_"):
            return IdentityLookupResult(status=IdentityStatus.AMBIGUO, confidence=0.5)
        return IdentityLookupResult(status=IdentityStatus.NO_IDENTIFICADO)


def _verificar_protocolos() -> None:  # pragma: no cover - solo para mypy
    from social_listener.application.ports.outbound.services import (
        IdentityService,
        Pseudonymizer,
        SensitiveDataDetector,
        SourceConnector,
    )

    _c: SourceConnector = ScriptedConnector([])
    _d: SensitiveDataDetector = NoDetections()
    _p: Pseudonymizer = HmacPseudonymizer()
    _l: LLMClassifier = ScriptedLLMClassifier()
    _i: IdentityService = PrefixIdentityService()
