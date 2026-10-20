"""Registro de proveedores de modelos y cadena de modelos por proyecto.

Agregar un proveedor nuevo es escribir un adaptador de ``LLMClassifier`` y
registrar aquí su fábrica y los campos de configuración que exige.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from social_listener.adapters.outbound.llm.anthropic_adapter import (
    AnthropicClassifier,
    bedrock_client,
    direct_client,
    vertex_client,
)
from social_listener.adapters.outbound.llm.chain import (
    ChainLink,
    FallbackChainClassifier,
    LinkObserver,
    NullClassifier,
)
from social_listener.adapters.outbound.llm.ollama_adapter import OllamaClassifier
from social_listener.adapters.outbound.llm.openai_compatible_adapter import (
    OpenAICompatibleClassifier,
    compatible_client,
)
from social_listener.adapters.outbound.secrets.provider import SecretProvider
from social_listener.application.dto.classification import ModelRoute
from social_listener.application.ports.outbound.services import LLMClassifier
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import ProjectId

Factory = Callable[[ModelRoute], LLMClassifier]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    factory: Factory
    requires_credential: bool = False
    requires_base_url: bool = False
    required_options: tuple[str, ...] = ()


class ProviderRegistry:
    def __init__(self, providers: Mapping[str, ProviderSpec]) -> None:
        self._providers = dict(providers)

    @property
    def names(self) -> list[str]:
        return sorted(self._providers)

    def validate(self, chain: Sequence[ModelRoute]) -> None:
        for position, route in enumerate(chain, start=1):
            spec = self._providers.get(route.provider)
            require(
                spec is not None,
                f"eslabón {position}: proveedor no soportado {route.provider!r} "
                f"(disponibles: {', '.join(self.names)})",
            )
            assert spec is not None  # noqa: S101
            require(bool(route.model.strip()), f"eslabón {position}: falta el modelo")
            require(route.timeout_s > 0, f"eslabón {position}: el timeout debe ser positivo")
            require(
                not spec.requires_credential or bool(route.credential_ref),
                f"eslabón {position}: {route.provider} requiere credential_ref",
            )
            require(
                not spec.requires_base_url or bool(route.base_url),
                f"eslabón {position}: {route.provider} requiere base_url",
            )
            missing = [o for o in spec.required_options if not route.options.get(o)]
            require(
                not missing,
                f"eslabón {position}: {route.provider} requiere {', '.join(missing)}",
            )

    def build(self, route: ModelRoute) -> LLMClassifier:
        return self._providers[route.provider].factory(route)


def default_registry(secrets: SecretProvider) -> ProviderRegistry:
    def secret(route: ModelRoute) -> str | None:
        return secrets.get(route.credential_ref) if route.credential_ref else None

    def anthropic(route: ModelRoute) -> LLMClassifier:
        api_key = secret(route)
        assert api_key is not None  # noqa: S101 - validado por requires_credential
        return AnthropicClassifier(direct_client(api_key), model=route.model, provider="anthropic")

    def anthropic_bedrock(route: ModelRoute) -> LLMClassifier:
        return AnthropicClassifier(
            bedrock_client(route.options["region"]),
            model=route.model,
            provider="anthropic_bedrock",
        )

    def anthropic_vertex(route: ModelRoute) -> LLMClassifier:
        return AnthropicClassifier(
            vertex_client(route.options["project_id"], route.options["region"]),
            model=route.model,
            provider="anthropic_vertex",
        )

    def ollama(route: ModelRoute) -> LLMClassifier:
        return OllamaClassifier(
            route.model,
            base_url=route.base_url or "http://localhost:11434",
            timeout_s=route.timeout_s,
            think="think" in route.capabilities,
        )

    def openai_compatible(route: ModelRoute) -> LLMClassifier:
        return OpenAICompatibleClassifier(
            compatible_client(base_url=route.base_url, api_key=secret(route)),
            model=route.model,
            capabilities=route.capabilities,
        )

    return ProviderRegistry(
        {
            "anthropic": ProviderSpec(anthropic, requires_credential=True),
            "anthropic_bedrock": ProviderSpec(anthropic_bedrock, required_options=("region",)),
            "anthropic_vertex": ProviderSpec(
                anthropic_vertex, required_options=("project_id", "region")
            ),
            "ollama": ProviderSpec(ollama),
            "openai_compatible": ProviderSpec(openai_compatible, requires_base_url=True),
        }
    )


class ProjectModelChains:
    """Implementa ``LLMClassifierProvider``: arma y cachea la cadena de cada proyecto.

    La configuración se relee en cada llamada (una consulta liviana); la cadena solo
    se reconstruye cuando cambia, así se conserva el estado de sus circuit breakers.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        registry: ProviderRegistry,
        *,
        observer: LinkObserver | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._observer = observer
        self._cache: dict[str, tuple[tuple[ModelRoute, ...], LLMClassifier]] = {}

    async def for_project(self, project_id: str) -> LLMClassifier:
        async with self._uow_factory() as uow:
            routes = tuple(await uow.model_chains.get(ProjectId(project_id)))
        cached = self._cache.get(project_id)
        if cached is not None and cached[0] == routes:
            return cached[1]
        classifier = self._build(routes)
        self._cache[project_id] = (routes, classifier)
        return classifier

    def _build(self, routes: tuple[ModelRoute, ...]) -> LLMClassifier:
        if not routes:
            return NullClassifier()
        links = [
            ChainLink(route.provider, route.model, self._registry.build(route), route.timeout_s)
            for route in routes
        ]
        if self._observer is None:
            return FallbackChainClassifier(links)
        return FallbackChainClassifier(links, observer=self._observer)
