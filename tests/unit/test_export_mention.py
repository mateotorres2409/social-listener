"""Escenarios de autor identificado y no identificado en la exportación."""

from __future__ import annotations

from datetime import timedelta

import pytest

from social_listener.application.dto.export import EventName
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.use_cases.export.assemble import (
    EnrichedMentionAssembler,
    author_pseudonym_of,
    customer_pseudonym_of,
)
from social_listener.application.use_cases.export.export import ExportMention
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.privacy.model import MaskedText, SensitiveDataType
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import HmacPseudonymizer, PrefixIdentityService
from tests.unit.test_identity_validation import validate
from tests.unit.test_individualize import process, seeded

PSEUDO = HmacPseudonymizer()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0 + timedelta(days=1))


async def export(db: InMemoryDatabase, clock: FixedClock, mention_id: str) -> None:
    async with db() as uow:
        await ExportMention(EnrichedMentionAssembler(PSEUDO), clock).handle(
            uow, Job(id="j", stage=Stage.EXPORT, subject_id=mention_id)
        )
        await uow.commit()


async def pipeline(clock: FixedClock, username: str) -> tuple[InMemoryDatabase, str]:
    db = await seeded(clock)
    mention_id = await process(db, clock, "m-1", author=("a-1", username))
    async with db() as uow:
        await uow.mentions.save_protection(
            mention_id,  # type: ignore[arg-type]
            MaskedText("tarjeta [TARJETA] bloqueada", frozenset({SensitiveDataType.TARJETA})),
        )
        await uow.commit()
    await validate(db, clock, mention_id, PrefixIdentityService())
    return db, mention_id


async def test_autor_identificado(clock: FixedClock) -> None:
    db, mention_id = await pipeline(clock, "cliente_ana")
    await export(db, clock, mention_id)

    (event,) = db.state.outbox
    assert event.name is EventName.MENTION_ENRICHED
    m = event.mention
    assert m is not None
    assert m.mention_id == mention_id
    assert m.identity_status is IdentityStatus.IDENTIFICADO
    assert m.customer_pseudonym == customer_pseudonym_of(PSEUDO, "ref-cliente_ana")
    assert m.author_pseudonym_id == author_pseudonym_of(PSEUDO, "x", "a-1")
    assert m.masked_text == "tarjeta [TARJETA] bloqueada"
    assert m.sensitive_data_types == ["tarjeta"]
    assert m.thread_id is not None
    assert "ref-cliente_ana" not in repr(m)
    assert "cliente_ana" not in repr(m)


async def test_autor_no_identificado(clock: FixedClock) -> None:
    db, mention_id = await pipeline(clock, "desconocido")
    await export(db, clock, mention_id)

    (event,) = db.state.outbox
    assert event.mention is not None
    assert event.mention.identity_status is IdentityStatus.NO_IDENTIFICADO
    assert event.mention.customer_pseudonym is None
