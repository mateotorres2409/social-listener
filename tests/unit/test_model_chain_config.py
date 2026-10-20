"""Configuración de la cadena de modelos por proyecto (escenarios de la spec)."""

from __future__ import annotations

from collections import Counter

import pytest

from social_listener.adapters.outbound.secrets.provider import StaticSecretProvider
from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.use_cases.classification.classify import ClassifyMention
from social_listener.bootstrap.models import (
    ProjectModelChains,
    ProviderRegistry,
    ProviderSpec,
    default_registry,
)
from social_listener.domain.classification.model import ClassificationSource, Dimension
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.privacy.model import MaskedText
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.errors import InvalidDomainValueError
from social_listener.domain.shared.types import ListeningQueryId, ProjectId
from tests.contracts.persistence import T0, incoming
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.unit.api_support import AUTH, api_client, fake_dependencies
from tests.unit.test_model_chain import respond

BANCO = ProjectId("p-banco")
FIDUCIARIA = ProjectId("p-fiduciaria")


class CountingRegistry:
    """Registro de prueba: cada proveedor responde con su nombre y cuenta las llamadas."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def registry(self) -> ProviderRegistry:
        def factory(route: ModelRoute):  # type: ignore[no-untyped-def]
            classifier = respond(route.provider, route.model)
            original = classifier.classify

            async def counted(request):  # type: ignore[no-untyped-def]
                self.calls[route.provider] += 1
                return await original(request)

            classifier.classify = counted  # type: ignore[method-assign]
            return classifier

        return ProviderRegistry(
            {
                "ollama": ProviderSpec(factory),
                "anthropic": ProviderSpec(factory, requires_credential=True),
                "openai_compatible": ProviderSpec(factory, requires_base_url=True),
            }
        )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
async def db(clock: FixedClock) -> InMemoryDatabase:
    db = InMemoryDatabase(clock)
    async with db() as uow:
        for pid in (BANCO, FIDUCIARIA):
            await uow.projects.save(Project(id=pid, name=pid))
            await uow.queries.add(
                ListeningQuery(
                    id=ListeningQueryId(f"q-{pid}"),
                    project_id=pid,
                    name="q",
                    channels=("x",),
                    terms=("b",),
                )
            )
        await uow.commit()
    return db


async def classify_for(db: InMemoryDatabase, chains: ProjectModelChains, project: ProjectId) -> str:
    base = incoming(f"m-{project}")
    async with db() as uow:
        mention, _ = await uow.mentions.add_or_get(project, base, ListeningQueryId(f"q-{project}"))
        await uow.mentions.save_protection(mention.id, MaskedText("la app no funciona"))
        await ClassifyMention(chains).handle(
            uow, Job(id="j", stage=Stage.CLASSIFY, subject_id=mention.id)
        )
        await uow.commit()
        result = await uow.classifications.get(mention.id)
    assert result is not None
    tipo = result.values[Dimension.TIPO]
    return tipo.provider if tipo.source is ClassificationSource.LLM else "respaldo"


async def put_chain(
    db: InMemoryDatabase,
    clock: FixedClock,
    registry: ProviderRegistry,
    project: ProjectId,
    chain: list[dict[str, object]],
) -> int:
    async with api_client(fake_dependencies(db, clock, registry=registry)) as api:
        response = await api.put(
            f"/v1/projects/{project}/model-chain", json={"chain": chain}, headers=AUTH
        )
    return response.status_code


async def test_proyecto_con_modelo_local(db: InMemoryDatabase, clock: FixedClock) -> None:
    counting = CountingRegistry()
    registry = counting.registry()
    status = await put_chain(
        db, clock, registry, BANCO, [{"provider": "ollama", "model": "qwen3:4b"}]
    )
    assert status == 200

    provider = await classify_for(db, ProjectModelChains(db, registry), BANCO)

    assert provider == "ollama"
    assert counting.calls == {"ollama": 1}


async def test_proyectos_con_proveedores_distintos(db: InMemoryDatabase, clock: FixedClock) -> None:
    counting = CountingRegistry()
    registry = counting.registry()
    await put_chain(
        db,
        clock,
        registry,
        BANCO,
        [{"provider": "anthropic", "model": "claude-haiku-5-5", "credential_ref": "anthropic-key"}],
    )
    await put_chain(
        db,
        clock,
        registry,
        FIDUCIARIA,
        [
            {
                "provider": "openai_compatible",
                "model": "Qwen/Qwen3-8B",
                "base_url": "http://vllm:8000/v1",
            }
        ],
    )
    chains = ProjectModelChains(db, registry)

    assert await classify_for(db, chains, BANCO) == "anthropic"
    assert await classify_for(db, chains, FIDUCIARIA) == "openai_compatible"


async def test_proveedor_no_soportado_mantiene_la_cadena_anterior(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    registry = CountingRegistry().registry()
    await put_chain(db, clock, registry, BANCO, [{"provider": "ollama", "model": "qwen3:4b"}])

    status = await put_chain(
        db, clock, registry, BANCO, [{"provider": "proveedor-x", "model": "m"}]
    )

    assert status == 422
    async with api_client(fake_dependencies(db, clock, registry=registry)) as api:
        current = (await api.get(f"/v1/projects/{BANCO}/model-chain", headers=AUTH)).json()
    assert [r["provider"] for r in current["chain"]] == ["ollama"]


async def test_sin_cadena_los_modelos_estan_desactivados(db: InMemoryDatabase) -> None:
    counting = CountingRegistry()
    assert await classify_for(db, ProjectModelChains(db, counting.registry()), BANCO) == "respaldo"
    assert not counting.calls


async def test_la_cadena_se_cachea_hasta_que_cambia(db: InMemoryDatabase) -> None:
    built: list[str] = []

    def factory(route: ModelRoute):  # type: ignore[no-untyped-def]
        built.append(route.model)
        return respond(route.provider, route.model)

    registry = ProviderRegistry({"ollama": ProviderSpec(factory)})
    chains = ProjectModelChains(db, registry)
    async with db() as uow:
        await uow.model_chains.save(BANCO, [ModelRoute("ollama", "a")])
        await uow.commit()

    first = await chains.for_project(BANCO)
    assert await chains.for_project(BANCO) is first
    async with db() as uow:
        await uow.model_chains.save(BANCO, [ModelRoute("ollama", "b")])
        await uow.commit()
    assert await chains.for_project(BANCO) is not first
    assert built == ["a", "b"]


@pytest.mark.parametrize(
    ("route", "mensaje"),
    [
        (ModelRoute("anthropic", "claude-haiku-5-5"), "credential_ref"),
        (ModelRoute("anthropic_bedrock", "anthropic.claude-haiku-5-5"), "region"),
        (
            ModelRoute("anthropic_vertex", "claude-haiku-5-5", options={"region": "us"}),
            "project_id",
        ),
        (ModelRoute("openai_compatible", "gpt"), "base_url"),
        (ModelRoute("ollama", "  "), "modelo"),
        (ModelRoute("proveedor-x", "m"), "no soportado"),
    ],
)
def test_validacion_del_registro_real(route: ModelRoute, mensaje: str) -> None:
    registry = default_registry(StaticSecretProvider({}))
    with pytest.raises(InvalidDomainValueError, match=mensaje):
        registry.validate([route])


def test_registro_real_arma_cada_proveedor() -> None:
    registry = default_registry(StaticSecretProvider({"anthropic-key": "sk-prueba"}))
    routes = [
        ModelRoute("anthropic", "claude-haiku-5-5", credential_ref="anthropic-key"),
        ModelRoute(
            "anthropic_bedrock", "anthropic.claude-haiku-5-5", options={"region": "us-east-1"}
        ),
        ModelRoute("ollama", "qwen3:4b", base_url="http://ollama:11434"),
        ModelRoute("openai_compatible", "gpt", base_url="http://vllm:8000/v1"),
    ]
    registry.validate(routes)
    for route in routes:
        assert hasattr(registry.build(route), "classify")
