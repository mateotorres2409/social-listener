"""Baterías de contratos de los puertos de servicios externos."""

from __future__ import annotations

import pytest

from social_listener.application.dto.classification import ClassificationRequest
from social_listener.application.dto.identity import IdentityLookup
from social_listener.application.errors import (
    IdentityServiceUnavailableError,
    InvalidModelOutputError,
    ModelUnavailableError,
)
from social_listener.application.ports.outbound.services import (
    IdentityService,
    LLMClassifier,
    Pseudonymizer,
    SourceConnector,
)
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.shared.types import ListeningQueryId, ProjectId

QUERY = ListeningQuery(
    id=ListeningQueryId("q"), project_id=ProjectId("p"), name="q", channels=("x",), terms=("b",)
)

REQUEST = ClassificationRequest(
    system="Clasifica la mención.",
    user="la app no me deja entrar",
    output_schema={"type": "object"},
)


class PseudonymizerContract:
    """Requiere los fixtures ``pseudonymizer`` y ``rotated_pseudonymizer``."""

    def test_estable(self, pseudonymizer: Pseudonymizer) -> None:
        assert pseudonymizer.pseudonymize("autor", "x:123") == pseudonymizer.pseudonymize(
            "autor", "x:123"
        )

    def test_distinto_entre_valores_y_espacios(self, pseudonymizer: Pseudonymizer) -> None:
        a = pseudonymizer.pseudonymize("autor", "x:123")
        assert a != pseudonymizer.pseudonymize("autor", "x:124")
        assert a != pseudonymizer.pseudonymize("cliente", "x:123")

    def test_no_contiene_el_valor(self, pseudonymizer: Pseudonymizer) -> None:
        assert "123" not in pseudonymizer.pseudonymize("autor", "x:123").split(":", 1)[1]

    def test_rotacion_de_clave(
        self, pseudonymizer: Pseudonymizer, rotated_pseudonymizer: Pseudonymizer
    ) -> None:
        assert rotated_pseudonymizer.key_id != pseudonymizer.key_id
        assert rotated_pseudonymizer.pseudonymize("autor", "x:1") != pseudonymizer.pseudonymize(
            "autor", "x:1"
        )


class IdentityServiceContract:
    """Requiere ``identity_service`` (semántica por prefijo del stub) e
    ``unavailable_identity_service``."""

    async def test_cliente_identificado(self, identity_service: IdentityService) -> None:
        result = await identity_service.lookup(IdentityLookup("x", "1", "cliente_ana"))
        assert result.status == "identificado"
        assert result.customer_ref
        assert result.confidence is not None
        assert 0 <= result.confidence <= 1

    async def test_ambiguo_sin_referencia(self, identity_service: IdentityService) -> None:
        result = await identity_service.lookup(IdentityLookup("x", "2", "ambiguo_luis"))
        assert result.status == "ambiguo"
        assert result.customer_ref is None

    async def test_no_identificado(self, identity_service: IdentityService) -> None:
        result = await identity_service.lookup(IdentityLookup("x", "3", "desconocido"))
        assert result.status == "no_identificado"
        assert result.customer_ref is None

    async def test_no_disponible(self, unavailable_identity_service: IdentityService) -> None:
        with pytest.raises(IdentityServiceUnavailableError):
            await unavailable_identity_service.lookup(IdentityLookup("x", "1", "cliente_ana"))


class LLMClassifierContract:
    """Requiere ``working_classifier`` y ``failing_classifier``."""

    async def test_propuesta_valida(self, working_classifier: LLMClassifier) -> None:
        proposal = await working_classifier.classify(REQUEST)
        assert proposal.provider
        assert proposal.model
        assert proposal.values
        for value in proposal.values.values():
            assert 0 <= value.confidence <= 1
        assert -1 <= proposal.sentiment_score <= 1

    async def test_falla_solo_con_errores_del_puerto(
        self, failing_classifier: LLMClassifier
    ) -> None:
        with pytest.raises((ModelUnavailableError, InvalidModelOutputError)):
            await failing_classifier.classify(REQUEST)


class SourceConnectorContract:
    """Requiere ``connector`` con al menos un lote disponible."""

    async def test_tiene_nombre(self, connector: SourceConnector) -> None:
        assert connector.name.strip()

    async def test_recorre_los_lotes_hasta_el_final(self, connector: SourceConnector) -> None:
        checkpoint: str | None = None
        total = 0
        for _ in range(1000):
            batch = await connector.fetch(QUERY, checkpoint)
            total += len(batch.mentions)
            assert batch.next_checkpoint is not None or not batch.mentions
            checkpoint = batch.next_checkpoint
            if not batch.has_more:
                break
        else:
            pytest.fail("el conector nunca indicó el final")
        assert total > 0
        again = await connector.fetch(QUERY, checkpoint)
        assert not again.has_more
