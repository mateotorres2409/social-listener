import pytest

from social_listener.bootstrap.settings import ConfigurationError, load_settings

ENTORNO_VALIDO = {
    "SL_DATABASE_URL": "postgresql+asyncpg://usuario:clave-db-secreta@localhost/sl",
    "SL_PSEUDONYMIZATION_KEY": "clave-hmac-super-secreta",
    "SL_PSEUDONYMIZATION_KEY_ID": "k1",
    "SL_API_TOKEN": "token-api-secreto",
}


def test_carga_configuracion_valida() -> None:
    settings = load_settings(ENTORNO_VALIDO)

    assert settings.pseudonymization_key_id == "k1"
    assert settings.api_token.get_secret_value() == "token-api-secreto"
    assert settings.environment == "local"


def test_secreto_faltante_produce_error_claro_sin_valores() -> None:
    entorno = {k: v for k, v in ENTORNO_VALIDO.items() if k != "SL_PSEUDONYMIZATION_KEY"}

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(entorno)

    mensaje = str(excinfo.value)
    assert "SL_PSEUDONYMIZATION_KEY es obligatoria" in mensaje
    for valor in entorno.values():
        assert valor not in mensaje


def test_valor_invalido_no_se_muestra() -> None:
    entorno = {**ENTORNO_VALIDO, "SL_IDENTITY_TIMEOUT_S": "-7-secreto"}

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(entorno)

    assert "SL_IDENTITY_TIMEOUT_S es inválida" in str(excinfo.value)
    assert "-7-secreto" not in str(excinfo.value)


def test_repr_de_settings_no_expone_secretos() -> None:
    settings = load_settings(ENTORNO_VALIDO)

    texto = repr(settings)
    assert "clave-hmac-super-secreta" not in texto
    assert "token-api-secreto" not in texto
