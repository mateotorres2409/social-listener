"""Escenarios de ``proteccion-datos`` sobre la etapa de protección."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from social_listener.adapters.outbound.privacy.rule_detector import RuleBasedDetector
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.ports.inbound.stages import PermanentStageError
from social_listener.application.use_cases.privacy.protect import ProtectMention
from social_listener.domain.privacy.model import SensitiveDataType
from social_listener.domain.shared.types import ListeningQueryId, ProjectId
from tests.contracts.persistence import T0, incoming
from tests.fakes.memory import FixedClock, InMemoryDatabase

USE_CASES = Path(__file__).parents[2] / "src/social_listener/application/use_cases"
RAW_TEXT_ALLOWED = {"privacy/protect.py"}


async def _mention_with_text(db: InMemoryDatabase, text: str) -> str:
    base = incoming("m-1")
    async with db() as uow:
        mention, _ = await uow.mentions.add_or_get(
            ProjectId("p"), type(base)(data=base.data, raw_text=text), ListeningQueryId("q")
        )
        await uow.commit()
    return mention.id


async def _protect(db: InMemoryDatabase, mention_id: str) -> None:
    async with db() as uow:
        await ProtectMention(RuleBasedDetector()).handle(
            uow, Job(id="j", stage=Stage.PROTECT, subject_id=mention_id)
        )
        await uow.commit()


async def test_tarjeta_en_una_queja_queda_enmascarada_y_el_original_se_conserva() -> None:
    db = InMemoryDatabase(FixedClock(T0))
    original = "me cobraron doble en la tarjeta 4111 1111 1111 1111"
    mention_id = await _mention_with_text(db, original)

    await _protect(db, mention_id)

    async with db() as uow:
        mention = await uow.mentions.get(mention_id)  # type: ignore[arg-type]
        assert mention is not None
        assert mention.text is not None
        assert mention.text.value == "me cobraron doble en la tarjeta [TARJETA]"
        assert mention.text.sensitive_exposure
        assert mention.text.detected == {SensitiveDataType.TARJETA}
        assert await uow.mentions.get_raw_text(mention_id) == original  # type: ignore[arg-type]


async def test_cliente_publica_su_cuenta_marca_exposicion_sin_valores() -> None:
    db = InMemoryDatabase(FixedClock(T0))
    mention_id = await _mention_with_text(db, "Mi número de cuenta es 0123456789")

    await _protect(db, mention_id)

    async with db() as uow:
        mention = await uow.mentions.get(mention_id)  # type: ignore[arg-type]
        assert mention is not None
        assert mention.text is not None
        assert mention.text.detected == {SensitiveDataType.CUENTA}
        assert "0123456789" not in mention.text.value


async def test_mencion_sin_texto_original_es_error_permanente() -> None:
    db = InMemoryDatabase(FixedClock(T0))
    with pytest.raises(PermanentStageError):
        await _protect(db, "no-existe")


def test_solo_la_proteccion_lee_el_texto_original() -> None:
    usos = set()
    for path in USE_CASES.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "get_raw_text":
                usos.add(str(path.relative_to(USE_CASES)))
    assert usos == RAW_TEXT_ALLOWED
