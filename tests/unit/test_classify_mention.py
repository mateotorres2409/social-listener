"""Escenarios de ``clasificacion-menciones`` sobre ``ClassifyMention`` con fakes."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
    ProposedValue,
)
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.errors import InvalidModelOutputError, ModelUnavailableError
from social_listener.application.ports.inbound.stages import PermanentStageError
from social_listener.application.use_cases.classification.classify import (
    ClassificationEvent,
    ClassifyMention,
)
from social_listener.domain.classification.model import (
    INDETERMINADO,
    ClassificationSource,
    Dimension,
    MentionClassification,
    Topic,
)
from social_listener.domain.mentions.model import AuthorSnapshot, ListeningQuery
from social_listener.domain.privacy.model import MaskedText, SensitiveDataType
from social_listener.domain.projects.model import Project
from tests.contracts.persistence import P1, Q1, T0, incoming
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import ScriptedLLMClassifier, StaticLLMProvider

MASKED = "me cobraron doble la cuota de manejo de la tarjeta [TARJETA], exijo devolución"


def proposal(**overrides: object) -> Callable[[ClassificationRequest], ClassificationProposal]:
    values = {
        Dimension.TIPO: ProposedValue("reclamo", 0.92),
        Dimension.SENTIMIENTO: ProposedValue("negativo", 0.88),
        Dimension.CATEGORIA: ProposedValue("tarifas_costos", 0.81),
        Dimension.TEMATICA: ProposedValue("cobro doble cuota de manejo", 0.75),
    }
    fields: dict[str, object] = {
        "values": values,
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "sentiment_score": -0.8,
        "subcategory": None,
        "topic_label": "cobro doble cuota de manejo",
    }
    fields.update(overrides)

    def respond(_: ClassificationRequest) -> ClassificationProposal:
        return ClassificationProposal(**fields)  # type: ignore[arg-type]

    return respond


async def seeded_db(clock: FixedClock | None = None) -> InMemoryDatabase:
    db = InMemoryDatabase(clock or FixedClock(T0))
    async with db() as uow:
        await uow.projects.save(Project(id=P1, name="Banco"))
        await uow.queries.add(
            ListeningQuery(id=Q1, project_id=P1, name="q", channels=("x",), terms=("b",))
        )
        await uow.commit()
    return db


@pytest.fixture
async def db() -> InMemoryDatabase:
    return await seeded_db()


async def classify(
    db: InMemoryDatabase,
    classifier: ScriptedLLMClassifier,
    *,
    followers: int | None = 20,
    text: str = MASKED,
    events: list[ClassificationEvent] | None = None,
) -> MentionClassification:
    base = incoming("m-1")
    data = type(base.data)(
        **{
            **{f: getattr(base.data, f) for f in base.data.__dataclass_fields__},
            "author": AuthorSnapshot("a-1", "juanp_secreto", followers=followers),
        }
    )
    async with db() as uow:
        mention, _ = await uow.mentions.add_or_get(P1, type(base)(data=data, raw_text="x"), Q1)
        detected = frozenset({SensitiveDataType.TARJETA}) if "[TARJETA]" in text else frozenset()
        await uow.mentions.save_protection(mention.id, MaskedText(text, detected))
        use_case = ClassifyMention(
            StaticLLMProvider(classifier),
            observer=(events.append if events is not None else lambda _: None),
        )
        await use_case.handle(uow, Job(id="j", stage=Stage.CLASSIFY, subject_id=mention.id))
        await uow.commit()
    async with db() as uow:
        result = await uow.classifications.get(mention.id)
    assert result is not None
    return result


async def test_clasificacion_por_un_modelo(db: InMemoryDatabase) -> None:
    events: list[ClassificationEvent] = []
    result = await classify(db, ScriptedLLMClassifier(responder=proposal()), events=events)

    assert set(result.values) == set(Dimension)
    tipo = result.values[Dimension.TIPO]
    assert (tipo.value, tipo.source) == ("reclamo", ClassificationSource.LLM)
    assert (tipo.provider, tipo.model) == ("anthropic", "claude-haiku-5-5")
    canal = result.values[Dimension.CANAL]
    assert (canal.value, canal.source, canal.confidence) == ("x", ClassificationSource.REGLA, 1.0)
    assert result.values[Dimension.TEMATICA].value == "cobro-doble-cuota-manejo"
    assert result.values[Dimension.IMPACTO].value == "medio"  # negativo de bajo alcance
    assert not result.needs_review
    assert events[0].outcome == "modelo"


async def test_el_modelo_recibe_solo_texto_enmascarado(db: InMemoryDatabase) -> None:
    classifier = ScriptedLLMClassifier(responder=proposal())
    await classify(db, classifier)

    (request,) = classifier.requests
    full = request.system + request.user
    assert "[TARJETA]" in full
    for forbidden in ("juanp_secreto", "a-1", "4111"):
        assert forbidden not in full


async def test_valor_fuera_de_la_taxonomia(db: InMemoryDatabase) -> None:
    values = {
        Dimension.TIPO: ProposedValue("reclamo", 0.9),
        Dimension.SENTIMIENTO: ProposedValue("negativo", 0.9),
        Dimension.CATEGORIA: ProposedValue("criptomonedas", 0.9),
        Dimension.TEMATICA: ProposedValue("x", 0.9),
    }
    result = await classify(db, ScriptedLLMClassifier(responder=proposal(values=values)))

    assert result.values[Dimension.CATEGORIA].value == INDETERMINADO
    assert result.needs_review


async def test_todos_los_modelos_no_disponibles(db: InMemoryDatabase) -> None:
    events: list[ClassificationEvent] = []
    result = await classify(
        db, ScriptedLLMClassifier(error=ModelUnavailableError("caído")), events=events
    )

    for dimension in (Dimension.TIPO, Dimension.SENTIMIENTO, Dimension.CATEGORIA):
        assert result.values[dimension].source is ClassificationSource.RESPALDO
    assert result.values[Dimension.TIPO].value == "reclamo"
    assert result.values[Dimension.CATEGORIA].value == "tarifas_costos"
    assert result.needs_review  # la confianza del respaldo es baja
    assert events[0].outcome == "respaldo"
    assert events[0].error == "ModelUnavailableError"


async def test_salida_invalida_usa_respaldo(db: InMemoryDatabase) -> None:
    result = await classify(db, ScriptedLLMClassifier(error=InvalidModelOutputError("x")))
    assert result.values[Dimension.TIPO].source is ClassificationSource.RESPALDO


async def test_sentimiento_incoherente_queda_indeterminado(db: InMemoryDatabase) -> None:
    result = await classify(
        db, ScriptedLLMClassifier(responder=proposal(sentiment_score=0.9))
    )  # "negativo" con puntaje positivo
    assert result.values[Dimension.SENTIMIENTO].value == INDETERMINADO
    assert result.needs_review


async def test_baja_confianza_marca_revision(db: InMemoryDatabase) -> None:
    values = {
        Dimension.TIPO: ProposedValue("queja", 0.3),
        Dimension.SENTIMIENTO: ProposedValue("negativo", 0.9),
        Dimension.CATEGORIA: ProposedValue("tarjetas", 0.9),
        Dimension.TEMATICA: ProposedValue("cobro", 0.9),
    }
    result = await classify(db, ScriptedLLMClassifier(responder=proposal(values=values)))
    assert result.needs_review


async def test_reutiliza_tematica_existente(db: InMemoryDatabase) -> None:
    async with db() as uow:
        await uow.topics.record_use(Topic(P1, "cobro-doble-cuota-manejo", "cobro doble"))
        await uow.commit()
    await classify(
        db, ScriptedLLMClassifier(responder=proposal(topic_label="Cobro doble de cuota manejo"))
    )
    async with db() as uow:
        (topic,) = await uow.topics.most_frequent(P1, 10)
    assert (topic.slug, topic.mentions_count) == ("cobro-doble-cuota-manejo", 2)


async def test_influenciador_y_subcategoria(db: InMemoryDatabase) -> None:
    values = {
        Dimension.TIPO: ProposedValue("queja", 0.9),
        Dimension.SENTIMIENTO: ProposedValue("negativo", 0.9),
        Dimension.CATEGORIA: ProposedValue("tarjetas", 0.9),
        Dimension.TEMATICA: ProposedValue("bloqueo tarjeta", 0.9),
    }
    result = await classify(
        db,
        ScriptedLLMClassifier(responder=proposal(values=values, subcategory="credito")),
        followers=80_000,
    )
    assert result.values[Dimension.IMPACTO].value in {"alto", "critico"}
    assert result.subcategory == "credito"


async def test_mencion_sin_proteccion_es_error_permanente(db: InMemoryDatabase) -> None:
    async with db() as uow:
        mention, _ = await uow.mentions.add_or_get(P1, incoming("m-9"), Q1)
        with pytest.raises(PermanentStageError):
            await ClassifyMention(StaticLLMProvider(ScriptedLLMClassifier())).handle(
                uow, Job(id="j", stage=Stage.CLASSIFY, subject_id=mention.id)
            )
