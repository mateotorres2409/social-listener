"""Adaptador de Ollama contra un transporte HTTP falso, y prueba opcional con Ollama real."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from social_listener.adapters.outbound.llm.ollama_adapter import OllamaClassifier
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.domain.classification.model import Dimension
from tests.contracts.services import LLMClassifierContract
from tests.unit.test_anthropic_adapter import REQUEST
from tests.unit.test_classification_prompt import VALID


def chat_response(content: str) -> dict[str, Any]:
    return {
        "model": "qwen3:4b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 900,
        "eval_count": 60,
    }


class Recorder:
    def __init__(self, *responses: dict[str, Any] | Exception | httpx.Response) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)


def classifier(recorder: Recorder) -> OllamaClassifier:
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    return OllamaClassifier("qwen3:4b", base_url="http://ollama:11434", client=client)


class TestOllamaContract(LLMClassifierContract):
    @pytest.fixture
    def working_classifier(self) -> OllamaClassifier:
        return classifier(Recorder(chat_response(json.dumps(VALID))))

    @pytest.fixture
    def failing_classifier(self) -> OllamaClassifier:
        return classifier(Recorder(httpx.ConnectError("sin ruta")))


async def test_solicitud_nativa_con_format_y_temperatura_cero() -> None:
    recorder = Recorder(chat_response(json.dumps(VALID)))

    proposal = await classifier(recorder).classify(REQUEST)

    (body,) = recorder.bodies
    assert body["model"] == "qwen3:4b"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"] == {"temperature": 0}
    assert body["format"]["type"] == "object"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert (proposal.provider, proposal.model) == ("ollama", "qwen3:4b")
    assert proposal.values[Dimension.CATEGORIA].value == "tarifas_costos"
    assert proposal.usage == {"input_tokens": 900, "output_tokens": 60}


async def test_reintento_de_correccion() -> None:
    recorder = Recorder(chat_response("{roto"), chat_response(json.dumps(VALID)))
    await classifier(recorder).classify(REQUEST)
    assert [m["role"] for m in recorder.bodies[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]


async def test_salida_invalida_dos_veces() -> None:
    with pytest.raises(InvalidModelOutputError):
        await classifier(Recorder(chat_response("x"), chat_response("y"))).classify(REQUEST)


async def test_timeout() -> None:
    with pytest.raises(ModelUnavailableError, match="ReadTimeout"):
        await classifier(Recorder(httpx.ReadTimeout("lento"))).classify(REQUEST)


async def test_modelo_no_descargado() -> None:
    recorder = Recorder(httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(ModelUnavailableError, match="404"):
        await classifier(recorder).classify(REQUEST)


@pytest.fixture
def ollama_url() -> Iterator[str]:
    """Un Ollama ya levantado (``SL_TEST_OLLAMA_URL``) o uno efímero en testcontainers
    (``SL_TEST_OLLAMA_CONTAINER=1``, descarga la imagen y el modelo)."""
    if url := os.environ.get("SL_TEST_OLLAMA_URL"):
        yield url
        return
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    with DockerContainer("ollama/ollama:0.12.3").with_exposed_ports(11434) as container:
        wait_for_logs(container, "Listening on", timeout=120)
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(11434)}"
        httpx.post(f"{url}/api/pull", json={"name": OLLAMA_MODEL, "stream": False}, timeout=1800)
        yield url


OLLAMA_MODEL = os.environ.get("SL_TEST_OLLAMA_MODEL", "qwen3:1.7b")


@pytest.mark.ollama
@pytest.mark.skipif(
    not (os.environ.get("SL_TEST_OLLAMA_URL") or os.environ.get("SL_TEST_OLLAMA_CONTAINER")),
    reason="define SL_TEST_OLLAMA_URL o SL_TEST_OLLAMA_CONTAINER=1 para probar contra Ollama real",
)
async def test_contra_ollama_real(ollama_url: str) -> None:
    real = OllamaClassifier(OLLAMA_MODEL, base_url=ollama_url, timeout_s=300)
    try:
        proposal = await real.classify(REQUEST)
    finally:
        await real.aclose()
    assert proposal.provider == "ollama"
    assert set(proposal.values) == {
        Dimension.TIPO,
        Dimension.SENTIMIENTO,
        Dimension.CATEGORIA,
        Dimension.TEMATICA,
    }
