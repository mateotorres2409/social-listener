"""Escenarios de la spec ``ingesta-menciones`` sobre los casos de uso con fakes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_listener.application.backoff import Backoff
from social_listener.application.dto.pipeline import Stage
from social_listener.application.dto.sources import RawMention
from social_listener.application.errors import NotFoundError, RateLimitedError, SourceError
from social_listener.application.use_cases.ingestion.ingest import (
    IngestMentions,
    ReprocessDeadLetter,
)
from social_listener.application.use_cases.ingestion.queries import (
    ListeningQueryInput,
    ManageListeningQueries,
)
from social_listener.domain.mentions.model import Channel, ContentFormat, QueryStatus
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.errors import InvalidDomainValueError
from social_listener.domain.shared.types import ProjectId
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import ScriptedConnector

P = ProjectId("p-1")


def raw(external_id: str = "m-1", **kw: object) -> RawMention:
    base: dict[str, object] = {
        "channel": "x",
        "external_id": external_id,
        "url": f"https://x.com/s/{external_id}",
        "text": "el banco me cobró doble",
        "language": "es",
        "published_at": datetime(2026, 10, 1, 10, tzinfo=UTC),
        "author_external_id": "a-1",
        "author_username": "@juanp",
        "author_followers": 120,
        "likes": 3,
    }
    base.update(kw)
    return RawMention(**base)  # type: ignore[arg-type]


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
async def db(clock: FixedClock) -> InMemoryDatabase:
    db = InMemoryDatabase(clock)
    async with db() as uow:
        await uow.projects.save(Project(id=P, name="Banco"))
        await uow.commit()
    return db


def manage(db: InMemoryDatabase) -> ManageListeningQueries:
    return ManageListeningQueries(db, known_connectors={"x", "mock", "fixture"})


async def _create_query(db: InMemoryDatabase, channels: tuple[str, ...] = ("x",)) -> str:
    query = await manage(db).create(P, ListeningQueryInput("Marca", channels, terms=("banco",)))
    return query.id


def _ingest(
    db: InMemoryDatabase, clock: FixedClock, connector: ScriptedConnector, **kw: object
) -> IngestMentions:
    return IngestMentions(db, {connector.name: connector}, clock, **kw)  # type: ignore[arg-type]


class TestTopicosDeEscucha:
    async def test_crear_topico_valido(self, db: InMemoryDatabase) -> None:
        query = await manage(db).create(
            P, ListeningQueryInput("Marca", ("x", "mock"), terms=("banco",))
        )
        assert query.status is QueryStatus.ACTIVE
        assert (await manage(db).get(query.id)).name == "Marca"

    async def test_rechazar_topico_sin_criterios(self, db: InMemoryDatabase) -> None:
        with pytest.raises(InvalidDomainValueError):
            await manage(db).create(P, ListeningQueryInput("Marca", ("x",)))
        assert await manage(db).list_by_project(P) == []

    async def test_rechazar_canal_sin_conector(self, db: InMemoryDatabase) -> None:
        with pytest.raises(InvalidDomainValueError, match="sin conector"):
            await manage(db).create(P, ListeningQueryInput("M", ("myspace",), terms=("b",)))

    async def test_proyecto_inexistente(self, db: InMemoryDatabase) -> None:
        with pytest.raises(NotFoundError):
            await manage(db).create(
                ProjectId("otro"), ListeningQueryInput("M", ("x",), terms=("b",))
            )

    async def test_topico_pausado_no_captura(self, db: InMemoryDatabase, clock: FixedClock) -> None:
        query_id = await _create_query(db)
        await manage(db).set_status(query_id, QueryStatus.PAUSED)  # type: ignore[arg-type]
        connector = ScriptedConnector([[raw()]], name="x")

        report = await _ingest(db, clock, connector).run_cycle()

        assert connector.calls == []
        assert report.created == 0


class TestIngesta:
    async def test_mencion_completa_se_normaliza_y_entra_al_pipeline(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        report = await _ingest(db, clock, ScriptedConnector([[raw()]], name="x")).run_cycle()

        assert report.created == 1
        async with db() as uow:
            mention = await uow.mentions.find_by_external(Channel.X, "m-1")
            assert mention is not None
            assert mention.data.author.username == "juanp"
            assert mention.data.author.followers == 120
            assert mention.data.format is ContentFormat.PUBLICACION
            assert await uow.mentions.get_raw_text(mention.id) == "el banco me cobró doble"
            assert [j.subject_id for j in uow.jobs.pending(Stage.PROTECT)] == [mention.id]

    async def test_campos_opcionales_ausentes(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        sin_metricas = raw(author_followers=None, likes=None)
        report = await _ingest(db, clock, ScriptedConnector([[sin_metricas]], name="x")).run_cycle()

        assert report.created == 1
        async with db() as uow:
            mention = await uow.mentions.find_by_external(Channel.X, "m-1")
            assert mention is not None
            assert mention.data.author.followers is None
            assert mention.data.engagement.interactions == 0

    async def test_respuesta_se_marca_por_su_padre(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        await _ingest(
            db, clock, ScriptedConnector([[raw("m-2", parent_external_id="m-1")]], name="x")
        ).run_cycle()
        async with db() as uow:
            mention = await uow.mentions.find_by_external(Channel.X, "m-2")
            assert mention is not None
            assert mention.data.format is ContentFormat.RESPUESTA

    async def test_mencion_invalida_va_a_cola_de_errores_y_el_lote_continua(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        lote = [raw("m-1"), raw(external_id=None), raw("m-3", text="  ")]
        report = await _ingest(db, clock, ScriptedConnector([lote], name="x")).run_cycle()

        assert (report.created, report.invalid) == (1, 2)
        async with db() as uow:
            letters = await uow.dead_letters.list_recent(10)
        assert {letter.stage for letter in letters} == {Stage.NORMALIZE}
        assert {letter.reason for letter in letters} == {"sin id externo", "sin texto"}

    async def test_misma_mencion_en_dos_topicos(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        q1 = await _create_query(db)
        q2 = await _create_query(db)
        connector = ScriptedConnector([[raw()]], name="x")
        connector_bis = ScriptedConnector([[raw()]], name="x")

        await _ingest(db, clock, connector).run_cycle()  # captura q1 y q2 en el mismo ciclo
        report = await _ingest(db, clock, connector_bis).run_cycle()

        assert report.created == 0
        async with db() as uow:
            mention = await uow.mentions.find_by_external(Channel.X, "m-1")
            assert mention is not None
            assert mention.query_ids == {q1, q2}
            assert len(uow.jobs.pending(Stage.PROTECT)) == 1

    async def test_reingesta_no_crea_ni_reencola(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        await _ingest(db, clock, ScriptedConnector([[raw()]], name="x")).run_cycle()
        async with db() as uow:
            (job,) = await uow.jobs.claim(Stage.PROTECT, 1)
            await uow.jobs.complete(job)
            uow.working.checkpoints.clear()  # se perdió el checkpoint: el lote llega otra vez
            await uow.commit()

        report = await _ingest(db, clock, ScriptedConnector([[raw()]], name="x")).run_cycle()

        assert (report.created, report.duplicates) == (0, 1)
        async with db() as uow:
            assert uow.jobs.pending(Stage.PROTECT) == []

    async def test_reanuda_desde_el_checkpoint(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        query_id = await _create_query(db)
        lotes = [[raw("m-1")], [raw("m-2")]]
        await _ingest(
            db, clock, ScriptedConnector(lotes, name="x"), max_batches_per_cycle=1
        ).run_cycle()

        reiniciado = ScriptedConnector(lotes, name="x")
        report = await _ingest(db, clock, reiniciado).run_cycle()

        assert reiniciado.calls[0] == "1"
        assert report.created == 1
        async with db() as uow:
            assert await uow.checkpoints.get(query_id, "x") == "2"  # type: ignore[arg-type]


class TestLimitesDeTasa:
    async def test_limite_de_tasa_espera_y_no_pierde_ni_duplica(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        sleeper = Sleeper()
        connector = ScriptedConnector(
            [RateLimitedError(retry_after_s=30), [raw("m-1"), raw("m-2")]], name="x"
        )
        report = await _ingest(
            db, clock, connector, sleep=sleeper, backoff=Backoff(base_s=1, jitter=0)
        ).run_cycle()

        assert sleeper.calls == [30]
        assert report.created == 2
        assert report.failures == []

    async def test_reintentos_agotados_conservan_el_checkpoint(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        query_id = await _create_query(db)
        errores: list[list[RawMention] | Exception] = [RateLimitedError() for _ in range(5)]

        class SiempreLimitado(ScriptedConnector):
            async def fetch(self, query, checkpoint):  # type: ignore[no-untyped-def]
                self.calls.append(checkpoint)
                raise RateLimitedError()

        connector = SiempreLimitado(errores, name="x")
        sleeper = Sleeper()
        report = await _ingest(
            db, clock, connector, sleep=sleeper, max_retries=3, backoff=Backoff(jitter=0)
        ).run_cycle()

        assert len(connector.calls) == 4
        assert sleeper.calls == [2, 4, 8]
        assert report.failures == [f"{query_id}:x: RateLimitedError"]
        async with db() as uow:
            assert await uow.checkpoints.get(query_id, "x") is None  # type: ignore[arg-type]

    async def test_error_definitivo_no_reintenta(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        sleeper = Sleeper()
        connector = ScriptedConnector([SourceError("credenciales")], name="x")
        report = await _ingest(db, clock, connector, sleep=sleeper).run_cycle()

        assert sleeper.calls == []
        assert len(report.failures) == 1


class TestColaDeErrores:
    async def test_reprocesar_mencion_de_normalizacion(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        await _create_query(db)
        await _ingest(
            db, clock, ScriptedConnector([[raw(language="es", url=None)]], name="x")
        ).run_cycle()
        async with db() as uow:
            (letter,) = await uow.dead_letters.list_recent(1)
            # El administrador corrige el origen y reprocesa: aquí simulamos la corrección.
            uow.working.dead_letters[letter.id] = type(letter)(
                **{
                    **{f: getattr(letter, f) for f in letter.__dataclass_fields__},
                    "payload": {
                        **letter.payload,
                        "raw": {**letter.payload["raw"], "url": "https://x.com/s/m-1"},  # type: ignore[dict-item]
                    },
                }
            )
            await uow.commit()

        result = await ReprocessDeadLetter(db, clock).execute(letter.id)

        assert result.accepted
        async with db() as uow:
            assert await uow.mentions.find_by_external(Channel.X, "m-1") is not None
            assert len(uow.jobs.pending(Stage.PROTECT)) == 1
            assert await uow.dead_letters.get(letter.id) is None

    async def test_reprocesar_desde_la_etapa_fallida(
        self, db: InMemoryDatabase, clock: FixedClock
    ) -> None:
        async with db() as uow:
            await uow.jobs.enqueue(Stage.CLASSIFY, "m-9")
            (job,) = await uow.jobs.claim(Stage.CLASSIFY, 1)
            await uow.jobs.fail(job, "definitivo", retry_at=None)
            (letter,) = await uow.dead_letters.list_recent(1)
            await uow.commit()

        result = await ReprocessDeadLetter(db, clock).execute(letter.id)

        assert result.stage is Stage.CLASSIFY
        async with db() as uow:
            assert [j.subject_id for j in uow.jobs.pending(Stage.CLASSIFY)] == ["m-9"]

    async def test_elemento_inexistente(self, db: InMemoryDatabase, clock: FixedClock) -> None:
        with pytest.raises(NotFoundError):
            await ReprocessDeadLetter(db, clock).execute("no-existe")
