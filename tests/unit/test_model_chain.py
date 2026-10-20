"""Escenarios de la cadena de modelos (``clasificacion-menciones``)."""

from __future__ import annotations

import asyncio

import pytest

from social_listener.adapters.outbound.llm.chain import (
    ChainLink,
    FallbackChainClassifier,
    NullClassifier,
)
from social_listener.adapters.outbound.resilience import CircuitBreaker
from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.domain.classification.model import ClassificationSource, Dimension
from tests.contracts.services import REQUEST, LLMClassifierContract
from tests.fakes.services import ScriptedLLMClassifier
from tests.unit.test_classify_mention import classify, proposal, seeded_db


def respond(provider: str, model: str) -> ScriptedLLMClassifier:
    def build(request: ClassificationRequest) -> ClassificationProposal:
        base = proposal()(request)
        return ClassificationProposal(
            values=base.values,
            provider=provider,
            model=model,
            sentiment_score=base.sentiment_score,
            topic_label=base.topic_label,
        )

    return ScriptedLLMClassifier(responder=build)


class Slow:
    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        await asyncio.sleep(5)
        raise AssertionError("no debió terminar")


def link(name: str, classifier: object, timeout_s: float = 1.0, **kw: object) -> ChainLink:
    provider, model = name.split("/")
    return ChainLink(provider, model, classifier, timeout_s, **kw)  # type: ignore[arg-type]


class TestChainContract(LLMClassifierContract):
    @pytest.fixture
    def working_classifier(self) -> FallbackChainClassifier:
        return FallbackChainClassifier([link("ollama/qwen3", respond("ollama", "qwen3"))])

    @pytest.fixture
    def failing_classifier(self) -> FallbackChainClassifier:
        return FallbackChainClassifier(
            [link("ollama/qwen3", ScriptedLLMClassifier(error=ModelUnavailableError("x")))]
        )


async def test_falla_del_proveedor_primario() -> None:
    events: list[tuple[str, str, str]] = []
    chain = FallbackChainClassifier(
        [
            link(
                "anthropic/claude-haiku-5-5",
                ScriptedLLMClassifier(error=ModelUnavailableError("x")),
            ),
            link("ollama/qwen3:4b", respond("ollama", "qwen3:4b")),
        ],
        observer=lambda provider, model, outcome, _: events.append((provider, model, outcome)),
    )
    db = await seeded_db()

    result = await classify(db, chain)  # type: ignore[arg-type]

    tipo = result.values[Dimension.TIPO]
    assert (tipo.provider, tipo.model) == ("ollama", "qwen3:4b")
    assert events == [
        ("anthropic", "claude-haiku-5-5", "no_disponible"),
        ("ollama", "qwen3:4b", "ok"),
    ]


async def test_timeout_por_eslabon() -> None:
    chain = FallbackChainClassifier(
        [link("lento/m", Slow(), timeout_s=0.05), link("ollama/q", respond("ollama", "q"))]
    )
    assert (await chain.classify(REQUEST)).provider == "ollama"


async def test_salida_invalida_pasa_al_siguiente() -> None:
    chain = FallbackChainClassifier(
        [
            link("a/m", ScriptedLLMClassifier(error=InvalidModelOutputError("x"))),
            link("b/m", respond("b", "m")),
        ]
    )
    assert (await chain.classify(REQUEST)).provider == "b"


async def test_todos_los_modelos_no_disponibles() -> None:
    chain = FallbackChainClassifier(
        [
            link("a/m", ScriptedLLMClassifier(error=ModelUnavailableError("x"))),
            link("b/m", ScriptedLLMClassifier(error=InvalidModelOutputError("y"))),
        ]
    )
    with pytest.raises(ModelUnavailableError):
        await chain.classify(REQUEST)

    db = await seeded_db()
    result = await classify(db, chain)  # type: ignore[arg-type]
    assert result.values[Dimension.TIPO].source is ClassificationSource.RESPALDO


async def test_circuito_abierto_evita_pagar_el_timeout() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, cooldown_s=30, clock=lambda: now[0])
    caido = ScriptedLLMClassifier(error=ModelUnavailableError("x"))
    chain = FallbackChainClassifier(
        [link("a/m", caido, breaker=breaker), link("b/m", respond("b", "m"))]
    )

    for _ in range(3):
        await chain.classify(REQUEST)
    assert len(caido.requests) == 2  # la tercera vez el circuito ya estaba abierto

    now[0] = 31.0  # vence el enfriamiento: se vuelve a probar el primario
    await chain.classify(REQUEST)
    assert len(caido.requests) == 3


async def test_modelos_desactivados() -> None:
    with pytest.raises(ModelUnavailableError, match="desactivados"):
        await NullClassifier().classify(REQUEST)

    db = await seeded_db()
    result = await classify(db, NullClassifier())  # type: ignore[arg-type]
    for dimension in (Dimension.TIPO, Dimension.SENTIMIENTO, Dimension.CATEGORIA):
        assert result.values[dimension].source is ClassificationSource.RESPALDO
