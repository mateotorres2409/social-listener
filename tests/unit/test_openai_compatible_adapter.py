"""Adaptador compatible con OpenAI contra respuestas falsas de estilo OpenAI y vLLM."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import openai
import pytest

from social_listener.adapters.outbound.llm.openai_compatible_adapter import (
    OpenAICompatibleClassifier,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.domain.classification.model import Dimension
from tests.contracts.services import LLMClassifierContract
from tests.unit.test_anthropic_adapter import REQUEST
from tests.unit.test_classification_prompt import VALID


def completion(
    content: str | None,
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
    usage: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "modelo",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
    }
    if usage:  # vLLM y otros servidores a veces omiten usage
        body["usage"] = {"prompt_tokens": 700, "completion_tokens": 50, "total_tokens": 750}
    return body


class Recorder:
    def __init__(self, *responses: dict[str, Any] | Exception | httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx2.Response):
            return item
        return httpx2.Response(200, json=item)


def classifier(
    recorder: Recorder, *, base_url: str = "https://api.openai.com/v1", **kw: Any
) -> OpenAICompatibleClassifier:
    client = openai.AsyncOpenAI(
        api_key="sk-prueba",
        base_url=base_url,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
    )
    return OpenAICompatibleClassifier(client, model=kw.pop("model", "gpt-modelo"), **kw)


class TestOpenAICompatibleContract(LLMClassifierContract):
    @pytest.fixture
    def working_classifier(self) -> OpenAICompatibleClassifier:
        return classifier(Recorder(completion(json.dumps(VALID))))

    @pytest.fixture
    def failing_classifier(self) -> OpenAICompatibleClassifier:
        return classifier(Recorder(httpx2.Response(503, json={"error": {"message": "x"}})))


async def test_estilo_openai_con_json_schema_estricto() -> None:
    recorder = Recorder(completion(json.dumps(VALID)))

    proposal = await classifier(recorder).classify(REQUEST)

    (body,) = recorder.bodies
    assert str(recorder.requests[0].url) == "https://api.openai.com/v1/chat/completions"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert "minimum" not in json.dumps(fmt["json_schema"]["schema"])
    assert body["temperature"] == 0
    assert (proposal.provider, proposal.model) == ("openai_compatible", "gpt-modelo")
    assert proposal.usage == {"input_tokens": 700, "output_tokens": 50}


async def test_estilo_vllm_con_json_object_y_sin_usage() -> None:
    recorder = Recorder(completion(json.dumps(VALID), usage=False))

    proposal = await classifier(
        recorder,
        base_url="http://vllm.interno:8000/v1",
        model="Qwen/Qwen3-8B",
        capabilities={"json_object"},
    ).classify(REQUEST)

    (body,) = recorder.bodies
    assert body["response_format"] == {"type": "json_object"}
    assert "Esquema JSON de la respuesta" in body["messages"][0]["content"]
    assert proposal.model == "Qwen/Qwen3-8B"
    assert proposal.values[Dimension.TIPO].value == "reclamo"
    assert proposal.usage == {}


async def test_reintento_de_correccion() -> None:
    recorder = Recorder(completion("{"), completion(json.dumps(VALID)))
    await classifier(recorder).classify(REQUEST)
    assert [m["role"] for m in recorder.bodies[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]


async def test_rechazo_no_se_reintenta() -> None:
    recorder = Recorder(completion(None, refusal="no puedo ayudar con eso"))
    with pytest.raises(InvalidModelOutputError, match="rechazó"):
        await classifier(recorder).classify(REQUEST)
    assert len(recorder.requests) == 1


async def test_respuesta_truncada() -> None:
    recorder = Recorder(completion('{"tipo"', finish_reason="length"), completion("x"))
    with pytest.raises(InvalidModelOutputError):
        await classifier(recorder).classify(REQUEST)


async def test_timeout() -> None:
    with pytest.raises(ModelUnavailableError, match="APITimeoutError"):
        await classifier(Recorder(httpx2.ReadTimeout("lento"))).classify(REQUEST)
