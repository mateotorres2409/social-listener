"""Adaptador de ``LLMClassifier`` para cualquier API compatible con OpenAI.

Cubre OpenAI, Azure OpenAI, vLLM, LM Studio, el endpoint compatible de Gemini y
otros, cambiando ``base_url``. Usa ``response_format`` de tipo ``json_schema``; si
el servidor solo admite ``json_object`` (capacidad ``json_object``), degrada a ese
modo y la validación común se encarga del resto.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.chat.completion_create_params import ResponseFormat

from social_listener.adapters.outbound.llm.schema import REPAIR_INSTRUCTION, strip_constraints
from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
)
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.use_cases.classification.prompt import parse_model_output

JSON_OBJECT_ONLY = "json_object"
SCHEMA_NAME = "clasificacion_mencion"


class OpenAICompatibleClassifier:
    provider = "openai_compatible"

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        *,
        model: str,
        timeout_s: float = 30.0,
        capabilities: Collection[str] = (),
        max_tokens: int = 2048,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_s = timeout_s
        self._json_object_only = JSON_OBJECT_ONLY in capabilities
        self._max_tokens = max_tokens

    def _response_format(self, request: ClassificationRequest) -> ResponseFormat:
        if self._json_object_only:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "schema": strip_constraints(request.output_schema),
                "strict": True,
            },
        }

    async def classify(self, request: ClassificationRequest) -> ClassificationProposal:
        system = request.system
        if self._json_object_only:
            # En modo json_object el esquema no viaja en response_format: va en el prompt.
            system += "\n\nEsquema JSON de la respuesta:\n" + json.dumps(
                strip_constraints(request.output_schema), ensure_ascii=False
            )
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.user},
        ]
        last_error: InvalidModelOutputError | None = None
        for _ in range(2):
            completion = await self._create(messages, self._response_format(request))
            content = ""
            try:
                content = self._content(completion)
                return parse_model_output(
                    content,
                    provider=self.provider,
                    model=self._model,
                    usage=self._usage(completion),
                )
            except InvalidModelOutputError as exc:
                last_error = exc
                if completion.choices and completion.choices[0].message.refusal:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": REPAIR_INSTRUCTION.format(error=exc)},
                ]
        assert last_error is not None  # noqa: S101
        raise last_error

    async def _create(
        self, messages: list[ChatCompletionMessageParam], response_format: ResponseFormat
    ) -> ChatCompletion:
        try:
            return await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format=response_format,
                temperature=0,
                max_tokens=self._max_tokens,
                timeout=self._timeout_s,
            )
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.APIStatusError,
        ) as exc:
            raise ModelUnavailableError(f"{self.provider}: {type(exc).__name__}") from None

    @staticmethod
    def _content(completion: ChatCompletion) -> str:
        if not completion.choices:
            raise InvalidModelOutputError("la respuesta no tiene opciones")
        choice = completion.choices[0]
        if choice.message.refusal:
            raise InvalidModelOutputError("el modelo rechazó la solicitud")
        if choice.finish_reason == "length":
            raise InvalidModelOutputError("respuesta truncada por longitud")
        if not choice.message.content:
            raise InvalidModelOutputError("la respuesta no contiene texto")
        return choice.message.content

    @staticmethod
    def _usage(completion: ChatCompletion) -> dict[str, int]:
        usage: Any = completion.usage
        if usage is None:
            return {}
        return {
            "input_tokens": int(usage.prompt_tokens or 0),
            "output_tokens": int(usage.completion_tokens or 0),
        }


def compatible_client(
    *, base_url: str | None, api_key: str | None, max_retries: int = 1
) -> openai.AsyncOpenAI:
    """``api_key`` puede ser cualquier valor para servidores locales sin autenticación."""
    return openai.AsyncOpenAI(
        base_url=base_url, api_key=api_key or "no-key", max_retries=max_retries
    )
