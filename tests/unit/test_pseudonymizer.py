import pytest

from social_listener.adapters.outbound.privacy.pseudonymizer import HmacPseudonymizer
from social_listener.adapters.outbound.secrets.provider import (
    EnvSecretProvider,
    SecretNotFoundError,
    StaticSecretProvider,
)
from tests.contracts.services import PseudonymizerContract

SECRETS = StaticSecretProvider(
    {"seudonimos": "clave-hmac-de-pruebas-123", "seudonimos-v2": "otra-clave-hmac-rotada-456"}
)


class TestHmacPseudonymizer(PseudonymizerContract):
    @pytest.fixture
    def pseudonymizer(self) -> HmacPseudonymizer:
        return HmacPseudonymizer(SECRETS, key_ref="seudonimos", key_id="k1")

    @pytest.fixture
    def rotated_pseudonymizer(self) -> HmacPseudonymizer:
        return HmacPseudonymizer(SECRETS, key_ref="seudonimos-v2", key_id="k2")


def test_formato_del_seudonimo() -> None:
    value = HmacPseudonymizer(SECRETS, key_ref="seudonimos", key_id="k1").pseudonymize("a", "x")
    key_id, digest = value.split(":")
    assert key_id == "k1"
    assert len(digest) == 32
    int(digest, 16)


def test_clave_demasiado_corta() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        HmacPseudonymizer(StaticSecretProvider({"k": "corta"}), key_ref="k", key_id="k1")


def test_secreto_faltante_no_expone_valores() -> None:
    provider = EnvSecretProvider({"SL_SECRET_OTRO": "valor-secreto"})
    with pytest.raises(SecretNotFoundError) as excinfo:
        provider.get("anthropic-api-key")
    assert "SL_SECRET_ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "valor-secreto" not in str(excinfo.value)
    assert EnvSecretProvider({"SL_SECRET_OTRO": "v"}).get("otro") == "v"
