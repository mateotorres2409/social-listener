"""Adaptador de Claude contra un transporte HTTP falso (sin red)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from social_listener.adapters.outbound.llm.anthropic_adapter import AnthropicClassifier
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.use_cases.classification.prompt import (
    ClassificationPromptBuilder,
    MentionContext,
)
from social_listener.domain.classification.catalog import DEFAULT_TAXONOMY
from social_listener.domain.classification.model import Dimension
from tests.contracts.services import LLMClassifierContract
from tests.unit.test_classification_prompt import VALID

REQUEST = ClassificationPromptBuilder().build(
    MentionContext("la app no me deja entrar [TELEFONO]", "x", "publicacion", "es", False),
    DEFAULT_TAXONOMY,
    [],
)

Handler = Callable[[httpx2.Request], httpx2.Response]


def message(text: str | None, stop_reason: str = "end_turn") -> dict[str, Any]:
    content = [{"type": "text", "text": text}] if text is not None else []
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 80,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 0,
        },
    }


class Recorder:
    def __init__(self, *responses: dict[str, Any] | Exception | httpx2.Response) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx2.Response):
            return item
        return httpx2.Response(200, json=item)


def classifier(handler: Handler) -> AnthropicClassifier:
    client = anthropic.AsyncAnthropic(
        api_key="sk-prueba",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicClassifier(client, timeout_s=5)


class TestAnthropicContract(LLMClassifierContract):
    @pytest.fixture
    def working_classifier(self) -> AnthropicClassifier:
        return classifier(Recorder(message(json.dumps(VALID))))

    @pytest.fixture
    def failing_classifier(self) -> AnthropicClassifier:
        return classifier(Recorder(httpx2.Response(529, json={"type": "error"})))


async def test_solicitud_con_salida_estructurada_cache_y_esfuerzo_bajo() -> None:
    recorder = Recorder(message(json.dumps(VALID)))

    proposal = await classifier(recorder).classify(REQUEST)

    (body,) = recorder.bodies
    assert body["model"] == "claude-haiku-5-5"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "minimum" not in json.dumps(schema)
    assert "maxLength" not in json.dumps(schema)
    assert body["messages"] == [{"role": "user", "content": REQUEST.user}]
    assert (proposal.provider, proposal.model) == ("anthropic", "claude-haiku-5-5")
    assert proposal.values[Dimension.TIPO].value == "reclamo"
    assert proposal.usage["cache_read_input_tokens"] == 1000


async def test_reintento_de_correccion() -> None:
    fuera_de_rango = {**VALID, "tipo": {"valor": "reclamo", "confianza": 7}}
    recorder = Recorder(message(json.dumps(fuera_de_rango)), message(json.dumps(VALID)))

    proposal = await classifier(recorder).classify(REQUEST)

    assert proposal.values[Dimension.TIPO].confidence == 0.9
    second = recorder.bodies[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert "no cumple el esquema" in second[2]["content"]


async def test_salida_invalida_dos_veces() -> None:
    recorder = Recorder(message("no es json"), message("tampoco"))
    with pytest.raises(InvalidModelOutputError):
        await classifier(recorder).classify(REQUEST)
    assert len(recorder.bodies) == 2


async def test_rechazo_no_se_reintenta() -> None:
    recorder = Recorder(message(None, stop_reason="refusal"))
    with pytest.raises(InvalidModelOutputError, match="rechazó"):
        await classifier(recorder).classify(REQUEST)
    assert len(recorder.bodies) == 1


async def test_timeout_es_modelo_no_disponible() -> None:
    recorder = Recorder(httpx2.ReadTimeout("lento"))
    with pytest.raises(ModelUnavailableError, match="APITimeoutError"):
        await classifier(recorder).classify(REQUEST)


async def test_limite_de_tasa_es_modelo_no_disponible_sin_detalles() -> None:
    recorder = Recorder(
        httpx2.Response(429, json={"type": "error", "error": {"message": "org-123 secreta"}})
    )
    with pytest.raises(ModelUnavailableError) as excinfo:
        await classifier(recorder).classify(REQUEST)
    assert "org-123" not in str(excinfo.value)
