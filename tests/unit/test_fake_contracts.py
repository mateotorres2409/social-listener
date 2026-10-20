"""Los fakes en memoria cumplen las mismas baterías de contratos que los adaptadores."""

from __future__ import annotations

import pytest

from social_listener.application.dto.classification import (
    ClassificationProposal,
    ClassificationRequest,
    ProposedValue,
)
from social_listener.application.dto.sources import RawMention
from social_listener.application.errors import ModelUnavailableError
from social_listener.domain.classification.model import Dimension
from tests.contracts.persistence import T0, PersistenceContract
from tests.contracts.services import (
    IdentityServiceContract,
    LLMClassifierContract,
    PseudonymizerContract,
    SourceConnectorContract,
)
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import (
    HmacPseudonymizer,
    PrefixIdentityService,
    ScriptedConnector,
    ScriptedLLMClassifier,
)


class TestInMemoryPersistence(PersistenceContract):
    @pytest.fixture
    def clock(self) -> FixedClock:
        return FixedClock(T0)

    @pytest.fixture
    def uow_factory(self, clock: FixedClock) -> InMemoryDatabase:
        return InMemoryDatabase(clock, max_attempts=5)


class TestHmacPseudonymizerFake(PseudonymizerContract):
    @pytest.fixture
    def pseudonymizer(self) -> HmacPseudonymizer:
        return HmacPseudonymizer()

    @pytest.fixture
    def rotated_pseudonymizer(self) -> HmacPseudonymizer:
        return HmacPseudonymizer(key=b"otra-clave", key_id="k-2")


class TestPrefixIdentityServiceFake(IdentityServiceContract):
    @pytest.fixture
    def identity_service(self) -> PrefixIdentityService:
        return PrefixIdentityService()

    @pytest.fixture
    def unavailable_identity_service(self) -> PrefixIdentityService:
        return PrefixIdentityService(unavailable=True)


def _respuesta(_: ClassificationRequest) -> ClassificationProposal:
    return ClassificationProposal(
        values={Dimension.TIPO: ProposedValue("queja", 0.8)},
        provider="fake",
        model="fake-1",
        sentiment_score=-0.6,
    )


class TestScriptedLLMClassifierFake(LLMClassifierContract):
    @pytest.fixture
    def working_classifier(self) -> ScriptedLLMClassifier:
        return ScriptedLLMClassifier(responder=_respuesta)

    @pytest.fixture
    def failing_classifier(self) -> ScriptedLLMClassifier:
        return ScriptedLLMClassifier(error=ModelUnavailableError("caído"))


class TestScriptedConnectorFake(SourceConnectorContract):
    @pytest.fixture
    def connector(self) -> ScriptedConnector:
        return ScriptedConnector([[RawMention(external_id="1")], [RawMention(external_id="2")]])
