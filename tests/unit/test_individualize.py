"""Escenarios de ``individualizacion-autores`` sobre ``IndividualizeAuthor`` con fakes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.use_cases.authors.individualize import IndividualizeAuthor
from social_listener.domain.mentions.model import (
    AuthorSnapshot,
    Channel,
    IncomingMention,
    ListeningQuery,
)
from social_listener.domain.projects.model import OfficialAccount, Project, RecurrenceRule
from tests.contracts.persistence import P1, Q1, T0, classification, incoming
from tests.fakes.memory import FixedClock, InMemoryDatabase


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0 + timedelta(days=1))


async def seeded(clock: FixedClock) -> InMemoryDatabase:
    db = InMemoryDatabase(clock)
    async with db() as uow:
        await uow.projects.save(
            Project(
                id=P1,
                name="Banco",
                recurrence=RecurrenceRule(threshold=2, window_days=30),
                official_accounts=frozenset({OfficialAccount(Channel.X, "banco-oficial")}),
            )
        )
        await uow.queries.add(
            ListeningQuery(id=Q1, project_id=P1, name="q", channels=("x",), terms=("b",))
        )
        await uow.commit()
    return db


@pytest.fixture
async def db(clock: FixedClock) -> InMemoryDatabase:
    return await seeded(clock)


async def process(
    db: InMemoryDatabase,
    clock: FixedClock,
    external_id: str,
    *,
    author: tuple[str, str] = ("a-1", "juanp"),
    parent: str | None = None,
    tipo: str = "queja",
    days: int = 0,
) -> str:
    base = incoming(external_id, published_at=T0 + timedelta(hours=days))
    data = replace(
        base.data,
        author=AuthorSnapshot(author[0], author[1], followers=10),
        parent_external_id=parent,
    )
    async with db() as uow:
        mention, _ = await uow.mentions.add_or_get(P1, IncomingMention(data, "texto"), Q1)
        await uow.classifications.save(classification(mention.id, tipo))
        await IndividualizeAuthor(clock).handle(
            uow, Job(id="j", stage=Stage.INDIVIDUALIZE, subject_id=mention.id)
        )
        await uow.commit()
    return mention.id


async def thread_of(db: InMemoryDatabase, mention_id: str) -> str | None:
    async with db() as uow:
        return await uow.mentions.thread_of(mention_id)  # type: ignore[arg-type]


async def test_primera_mencion_de_un_autor(db: InMemoryDatabase, clock: FixedClock) -> None:
    mention_id = await process(db, clock, "m-1")
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert author.total_mentions == 1
        assert await uow.mentions.author_of(mention_id) == author.id  # type: ignore[arg-type]


async def test_cambio_de_username(db: InMemoryDatabase, clock: FixedClock) -> None:
    first = await process(db, clock, "m-1")
    second = await process(db, clock, "m-2", author=("a-1", "juan_perez"), days=1)
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert author.username == "juan_perez"
        assert [c.username for c in author.previous_usernames] == ["juanp"]
        assert await uow.mentions.author_of(first) == author.id  # type: ignore[arg-type]
        assert await uow.mentions.author_of(second) == author.id  # type: ignore[arg-type]


async def test_actualizacion_del_perfil(db: InMemoryDatabase, clock: FixedClock) -> None:
    await process(db, clock, "m-1", tipo="queja")
    await process(db, clock, "m-2", tipo="felicitacion", days=1)
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert author.total_mentions == 2
        assert author.type_counts == {"queja": 1, "felicitacion": 1}
        assert author.sentiment_trend is not None


async def test_autor_con_quejas_repetidas(db: InMemoryDatabase, clock: FixedClock) -> None:
    for i in range(2):
        await process(db, clock, f"m-{i}", tipo="queja", days=i)
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert not author.recurrent  # 2 quejas no superan el umbral de 2

    await process(db, clock, "m-9", tipo="reclamo", days=3)
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert author.recurrent


async def test_respuesta_a_una_publicacion(db: InMemoryDatabase, clock: FixedClock) -> None:
    root = await process(db, clock, "p-1")
    reply = await process(db, clock, "r-1", author=("a-2", "luis"), parent="p-1", days=1)
    nested = await process(db, clock, "r-2", author=("a-3", "ana"), parent="r-1", days=2)
    assert await thread_of(db, root) == await thread_of(db, reply) == await thread_of(db, nested)


async def test_respuesta_que_llega_antes_que_el_padre(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    reply = await process(db, clock, "r-1", author=("a-2", "luis"), parent="p-1", days=1)
    nested = await process(db, clock, "r-2", author=("a-3", "ana"), parent="r-1", days=2)
    root = await process(db, clock, "p-1", days=0)

    assert await thread_of(db, root) == await thread_of(db, reply) == await thread_of(db, nested)
    async with db() as uow:
        assert await uow.threads.get_by_root(P1, Channel.X, "p-1") is not None


async def test_respuesta_intermedia_que_llega_al_final(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    root = await process(db, clock, "p-1")
    nested = await process(db, clock, "r-2", author=("a-3", "ana"), parent="r-1", days=2)
    reply = await process(db, clock, "r-1", author=("a-2", "luis"), parent="p-1", days=1)

    assert await thread_of(db, root) == await thread_of(db, reply) == await thread_of(db, nested)
    async with db() as uow:
        # El hilo provisional que se ancló en r-1 se fusionó y ya no existe.
        assert await uow.threads.get_by_root(P1, Channel.X, "r-1") is None


async def test_la_entidad_responde_una_queja(db: InMemoryDatabase, clock: FixedClock) -> None:
    complaint = await process(db, clock, "q-1")
    await process(
        db, clock, "o-1", author=("banco-oficial", "banco"), parent="q-1", tipo="informativa"
    )
    async with db() as uow:
        thread_id = await uow.mentions.thread_of(complaint)  # type: ignore[arg-type]
        assert thread_id is not None
        thread = await uow.threads.get(thread_id)
        assert thread is not None
        assert thread.attended_by_entity
        official = await uow.authors.get_by_external(P1, Channel.X, "banco-oficial")
        assert official is not None
        assert official.is_official
