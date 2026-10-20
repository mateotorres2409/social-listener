"""Adaptador de ``LLMClassifier`` para Claude (API directa, Amazon Bedrock o Vertex AI).

- Salida estructurada con ``output_config.format`` (JSON Schema común).
- Prompt caching del bloque de sistema, que cambia poco (definiciones, taxonomía, temáticas).
- Esfuerzo bajo por defecto: la clasificación no necesita razonamiento profundo.
- Un único reintento de corrección si la respuesta no pasa la validación común.
"""

from __future__ import annotations

from typing import Literal

import anthropic
from anthropic.types import MessageParam, OutputConfigParam, TextBlockParam

from social_listener.adapters.outbound.llm.schema import REPAIR_INSTRUCTION, strip_constraints
from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.use_cases.classification.prompt import parse_model_output

DEFAULT_MODEL = "claude-haiku-5-5"

Effort = Literal["low", "medium", "high", "xhigh", "max"]

AnthropicClient = (
    anthropic.AsyncAnthropic
    | anthropic.AsyncAnthropicBedrockMantle
    | anthropic.AsyncAnthropicVertex
)


class AnthropicClassifier:
    def __init__(
        self,
        client: AnthropicClient,
        *,
        model: str = DEFAULT_MODEL,
        provider: str = "anthropic",
        timeout_s: float = 20.0,
        max_tokens: int = 4096,
        effort: Effort = "low",
    ) -> None:
        self._client = client
        self._model = model
        self._provider = provider
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._effort = effort

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        system: list[TextBlockParam] = [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[MessageParam] = [{"role": "user", "content": request.user}]
        output_config: OutputConfigParam = {
            "effort": self._effort,
            "format": {"type": "json_schema", "schema": strip_constraints(request.output_schema)},
        }
        last_error: InvalidModelOutputError | None = None
        for _ in range(2):
            response = await self._create(system, messages, output_config)
            try:
                return self._parse(response)
            except InvalidModelOutputError as exc:
                last_error = exc
                if response.stop_reason == "refusal":
                    break  # un rechazo no se corrige reintentando
                messages = [
                    *messages,
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": REPAIR_INSTRUCTION.format(error=exc)},
                ]
        assert last_error is not None  # noqa: S101
        raise last_error

    async def _create(
        self,
        system: list[TextBlockParam],
        messages: list[MessageParam],
        output_config: OutputConfigParam,
    ) -> anthropic.types.Message:
        try:
            return await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
                timeout=self._timeout_s,
            )
        except (
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.APIStatusError,
        ) as exc:
            # Solo el tipo: el detalle puede incluir cabeceras o identificadores de la cuenta.
            raise ModelUnavailableError(f"{self._provider}: {type(exc).__name__}") from None

    def _parse(self, response: anthropic.types.Message) -> ClassificationProposal:
        if response.stop_reason == "refusal":
            raise InvalidModelOutputError("el modelo rechazó la solicitud")
        if response.stop_reason == "max_tokens":
            raise InvalidModelOutputError("respuesta truncada por max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise InvalidModelOutputError("la respuesta no contiene texto")
        usage = response.usage
        return parse_model_output(
            text,
            provider=self._provider,
            model=self._model,
            usage={
                "input_tokens": int(usage.input_tokens or 0),
                "output_tokens": int(usage.output_tokens or 0),
                "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
            },
        )


def direct_client(api_key: str, *, max_retries: int = 1) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=api_key, max_retries=max_retries)


def bedrock_client(region: str, *, max_retries: int = 1) -> anthropic.AsyncAnthropicBedrockMantle:
    """Claude en Amazon Bedrock (los ids de modelo llevan el prefijo ``anthropic.``)."""
    return anthropic.AsyncAnthropicBedrockMantle(aws_region=region, max_retries=max_retries)


def vertex_client(
    project_id: str, region: str, *, max_retries: int = 1
) -> anthropic.AsyncAnthropicVertex:
    """Claude en Google Vertex AI. Requiere ``anthropic[vertex]`` y credenciales ADC."""
    return anthropic.AsyncAnthropicVertex(
        project_id=project_id, region=region, max_retries=max_retries
    )
