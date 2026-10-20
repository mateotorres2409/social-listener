import io
import logging

from social_listener.adapters.outbound.logging.redaction import RedactingFilter, install_redaction

SECRETO = "sk-ant-api03-SUPERSECRETO123"


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.getLogger(f"prueba-{id(stream)}")
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    install_redaction([SECRETO], logger)
    return logger, stream


def test_error_de_autenticacion_no_imprime_la_credencial() -> None:
    logger, stream = _logger()

    logger.error("la fuente x rechazó las credenciales %s", SECRETO)
    logger.warning("solicitud con Authorization: Bearer abcdefghijklmnop123")
    logger.warning("url postgresql+asyncpg://sl:clave-db@db/sl api_key=xyz123456")

    salida = stream.getvalue()
    assert SECRETO not in salida
    assert "abcdefghijklmnop123" not in salida
    assert "clave-db" not in salida
    assert "xyz123456" not in salida
    assert "la fuente x rechazó las credenciales [REDACTADO]" in salida


def test_traza_de_excepcion_redactada() -> None:
    logger, stream = _logger()
    try:
        raise RuntimeError(f"token={SECRETO}")
    except RuntimeError:
        logger.exception("falló la llamada")

    assert SECRETO not in stream.getvalue()


def test_texto_sin_secretos_no_cambia() -> None:
    assert RedactingFilter([SECRETO]).redact("ciclo de ingesta: 3 nuevas") == (
        "ciclo de ingesta: 3 nuevas"
    )
