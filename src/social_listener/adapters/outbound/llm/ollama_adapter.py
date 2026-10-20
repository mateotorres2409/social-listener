"""Adaptador de ``LLMClassifier`` para Ollama (API nativa ``/api/chat``).

Permite clasificar con modelos desplegados en la infraestructura de la entidad,
sin que el texto salga del perímetro. La salida se fuerza con ``format`` (JSON
Schema) y temperatura 0.
"""

from __future__ import annotations

from typing import Any

import httpx

from social_listener.adapters.outbound.llm.schema import REPAIR_INSTRUCTION, strip_constraints
from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.use_cases.classification.prompt import parse_model_output


class OllamaClassifier:
    provider = "ollama"

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        think: bool = False,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = client or httpx.AsyncClient()
        self._think = think

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]
        schema = strip_constraints(request.output_schema)
        last_error: InvalidModelOutputError | None = None
        for _ in range(2):
            data = await self._chat(messages, schema)
            content = str((data.get("message") or {}).get("content") or "")
            try:
                return parse_model_output(
                    content,
                    provider=self.provider,
                    model=self._model,
                    usage={
                        "input_tokens": int(data.get("prompt_eval_count") or 0),
                        "output_tokens": int(data.get("eval_count") or 0),
                    },
                )
            except InvalidModelOutputError as exc:
                last_error = exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": REPAIR_INSTRUCTION.format(error=exc)},
                ]
        assert last_error is not None  # noqa: S101
        raise last_error

    async def _chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "think": self._think,  # clasificar no requiere razonamiento extendido
            "options": {"temperature": 0},
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/api/chat", json=payload, timeout=self._timeout_s
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(f"ollama: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ModelUnavailableError(f"ollama: HTTP {response.status_code}")
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            raise InvalidModelOutputError("ollama devolvió una respuesta que no es JSON") from None
        return data

    async def aclose(self) -> None:
        await self._client.aclose()
