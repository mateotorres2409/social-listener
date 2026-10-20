"""Escenarios de ``validacion-identidad`` con un ``IdentityService`` falso."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from social_listener.adapters.inbound.worker.runner import StageRunner
from social_listener.application.backoff import Backoff
from social_listener.application.dto.export import EventName
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.use_cases.authors.identity import (
    RetryIdentityValidation,
    RevalidateIdentity,
    ValidateIdentity,
)
from social_listener.application.use_cases.export.assemble import (
    author_pseudonym_of,
    customer_pseudonym_of,
)
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.mentions.model import Channel
from tests.contracts.persistence import P1, T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import HmacPseudonymizer, PrefixIdentityService
from tests.unit.test_individualize import process, seeded


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0 + timedelta(days=1))


@pytest.fixture
async def db(clock: FixedClock) -> InMemoryDatabase:
    return await seeded(clock)


async def new_mention(
    db: InMemoryDatabase, clock: FixedClock, username: str, external_id: str = "m-1", **kw: object
) -> str:
    return await process(db, clock, external_id, author=("a-1", username), **kw)  # type: ignore[arg-type]


async def validate(
    db: InMemoryDatabase, clock: FixedClock, mention_id: str, service: PrefixIdentityService
) -> None:
    async with db() as uow:
        await ValidateIdentity(service, HmacPseudonymizer(), clock).handle(
            uow, Job(id="j", stage=Stage.IDENTITY, subject_id=mention_id)
        )
        await uow.commit()


async def status_of(db: InMemoryDatabase) -> tuple[IdentityStatus, str | None, float | None]:
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        result = await uow.identity_results.get(author.id)
    assert result is not None
    return result.status, result.customer_ref, result.confidence


async def test_autor_nuevo_consulta_con_datos_minimos(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    service = PrefixIdentityService()
    await validate(db, clock, await new_mention(db, clock, "cliente_ana"), service)

    (lookup,) = service.lookups
    assert (lookup.channel, lookup.external_author_id, lookup.username) == (
        "x",
        "a-1",
        "cliente_ana",
    )
    assert set(lookup.__dataclass_fields__) == {"channel", "external_author_id", "username"}
    assert await status_of(db) == (IdentityStatus.IDENTIFICADO, "ref-cliente_ana", 0.95)


@pytest.mark.parametrize(
    ("username", "status"),
    [("ambiguo_luis", IdentityStatus.AMBIGUO), ("desconocido", IdentityStatus.NO_IDENTIFICADO)],
)
async def test_ambiguo_y_no_cliente_sin_referencia(
    db: InMemoryDatabase, clock: FixedClock, username: str, status: IdentityStatus
) -> None:
    await validate(db, clock, await new_mention(db, clock, username), PrefixIdentityService())
    found, ref, _ = await status_of(db)
    assert (found, ref) == (status, None)


async def test_autor_ya_validado_no_consulta(db: InMemoryDatabase, clock: FixedClock) -> None:
    service = PrefixIdentityService()
    await validate(db, clock, await new_mention(db, clock, "cliente_ana"), service)
    second = await new_mention(db, clock, "cliente_ana", "m-2", days=1)
    await validate(db, clock, second, service)
    assert len(service.lookups) == 1


async def test_ttl_vencido_revalida(db: InMemoryDatabase, clock: FixedClock) -> None:
    service = PrefixIdentityService()
    await validate(db, clock, await new_mention(db, clock, "desconocido"), service)
    clock.advance(days=8)  # no_identificado vive 7 días
    await validate(db, clock, await new_mention(db, clock, "desconocido", "m-2", days=2), service)
    assert len(service.lookups) == 2


async def test_cambio_de_username_invalida_el_resultado(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    service = PrefixIdentityService()
    await validate(db, clock, await new_mention(db, clock, "cliente_ana"), service)
    renamed = await new_mention(db, clock, "ana_nueva", "m-2", days=1)
    await validate(db, clock, renamed, service)
    assert [lookup.username for lookup in service.lookups] == ["cliente_ana", "ana_nueva"]
    assert (await status_of(db))[0] is IdentityStatus.NO_IDENTIFICADO


async def test_microservicio_lento_deja_pendiente_y_programa_reintento(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    mention_id = await new_mention(db, clock, "lento_pedro")
    await validate(db, clock, mention_id, PrefixIdentityService())

    assert (await status_of(db))[0] is IdentityStatus.PENDIENTE
    async with db() as uow:
        (retry,) = uow.jobs.pending(Stage.IDENTITY_RETRY)
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
        assert retry.subject_id == author.id


async def test_resolucion_tardia_emite_evento(db: InMemoryDatabase, clock: FixedClock) -> None:
    await validate(
        db,
        clock,
        await new_mention(db, clock, "cliente_ana"),
        PrefixIdentityService(unavailable=True),
    )
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
    pseudonymizer = HmacPseudonymizer()

    clock.advance(minutes=5)
    runner = StageRunner(
        Stage.IDENTITY_RETRY,
        RetryIdentityValidation(PrefixIdentityService(), pseudonymizer, clock, max_attempts=3),
        db,
        clock,
    )
    assert await runner.run_once()

    assert (await status_of(db))[0] is IdentityStatus.IDENTIFICADO
    (event,) = db.state.outbox
    assert event.name is EventName.AUTHOR_IDENTITY_RESOLVED
    assert event.identity is not None
    assert event.identity.author_pseudonym_id == author_pseudonym_of(pseudonymizer, "x", "a-1")
    assert event.identity.customer_pseudonym == customer_pseudonym_of(
        pseudonymizer, "ref-cliente_ana"
    )
    assert "ref-cliente_ana" not in str(event)


async def test_reintentos_agotados_dejan_error_y_se_revalida_manualmente(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    caido = PrefixIdentityService(unavailable=True)
    await validate(db, clock, await new_mention(db, clock, "cliente_ana"), caido)
    runner = StageRunner(
        Stage.IDENTITY_RETRY,
        RetryIdentityValidation(caido, HmacPseudonymizer(), clock, max_attempts=3),
        db,
        clock,
        backoff=Backoff(base_s=1, jitter=0),
    )
    for _ in range(3):
        clock.advance(hours=1)
        assert await runner.run_once()

    assert (await status_of(db))[0] is IdentityStatus.ERROR
    assert db.state.dead_letters == {}

    # Un autor en error no se reconsulta solo con menciones nuevas.
    service = PrefixIdentityService()
    await validate(db, clock, await new_mention(db, clock, "cliente_ana", "m-2", days=1), service)
    assert service.lookups == []

    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
        assert author is not None
    await RevalidateIdentity(db, clock).execute(author.id)
    ok = StageRunner(
        Stage.IDENTITY_RETRY,
        RetryIdentityValidation(service, HmacPseudonymizer(), clock, max_attempts=3),
        db,
        clock,
    )
    assert await ok.run_once()
    assert (await status_of(db))[0] is IdentityStatus.IDENTIFICADO


async def test_cuenta_oficial_no_se_valida(db: InMemoryDatabase, clock: FixedClock) -> None:
    service = PrefixIdentityService()
    official = await process(db, clock, "o-1", author=("banco-oficial", "cliente_banco"))
    await validate(db, clock, official, service)
    assert service.lookups == []
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "banco-oficial")
        assert author is not None
        result = await uow.identity_results.get(author.id)
        assert result is not None
        assert result.status is IdentityStatus.NO_IDENTIFICADO


def test_ttl_del_estado_error_es_permanente() -> None:
    from social_listener.domain.identity.model import IdentityResult
    from social_listener.domain.identity.policies import IdentityTtlPolicy
    from social_listener.domain.shared.types import AuthorId

    result = IdentityResult(AuthorId("a"), IdentityStatus.ERROR, T0, "u")
    policy = IdentityTtlPolicy()
    assert policy.is_fresh(result, username="u", now=T0 + timedelta(days=999))
    assert not policy.is_fresh(
        replace(result, status=IdentityStatus.PENDIENTE), username="u", now=T0
    )
