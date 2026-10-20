"""Casos en español del detector por reglas (escenarios de ``proteccion-datos``)."""

import pytest

from social_listener.adapters.outbound.privacy.rule_detector import RuleBasedDetector
from social_listener.domain.privacy.model import SensitiveDataType as T
from social_listener.domain.privacy.policies import mask

detector = RuleBasedDetector()


def enmascarar(texto: str) -> str:
    return mask(texto, detector.detect(texto)).value


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        (
            "me cobraron doble en la tarjeta 4111 1111 1111 1111",
            "me cobraron doble en la tarjeta [TARJETA]",
        ),
        ("tarjeta 4111-1111-1111-1111 bloqueada", "tarjeta [TARJETA] bloqueada"),
        ("mi amex 378282246310005 no pasa", "mi amex [TARJETA] no pasa"),
        ("escríbanme a ana.perez@correo.com.co", "escríbanme a [EMAIL]"),
        ("mi cel es 300 123 4567", "mi cel es [TELEFONO]"),
        ("llamen al +57 310-555-1234 por favor", "llamen al [TELEFONO] por favor"),
        ("o al fijo 601 555 1234", "o al fijo [TELEFONO]"),
        ("mi cédula es 1.020.345.678", "mi cédula es [DOCUMENTO]"),
        ("CC 52345678 a nombre de mi mamá", "CC [DOCUMENTO] a nombre de mi mamá"),
        ("el NIT de la empresa es 900.123.456-7", "el NIT de la empresa es [DOCUMENTO]"),
        ("Mi número de cuenta es 0123456789", "Mi número de cuenta es [CUENTA]"),
        ("cuenta de ahorros 123-456789-01 sin fondos", "cuenta de ahorros [CUENTA] sin fondos"),
    ],
)
def test_enmascaramiento(texto: str, esperado: str) -> None:
    assert enmascarar(texto) == esperado


def test_dieciseis_digitos_sin_luhn_no_son_tarjeta() -> None:
    texto = "radicado 1234567890123456"
    tipos = {d.kind for d in detector.detect(texto)}

    assert T.TARJETA not in tipos
    assert enmascarar(texto) == "radicado [CUENTA]"  # igual queda protegido


def test_documento_telefono_y_correo_juntos() -> None:
    texto = "Soy CC 1020345678, cel 3001234567, correo luis@banco.co"
    resultado = mask(texto, detector.detect(texto))

    assert resultado.value == "Soy CC [DOCUMENTO], cel [TELEFONO], correo [EMAIL]"
    assert resultado.detected == {T.DOCUMENTO, T.TELEFONO, T.EMAIL}


@pytest.mark.parametrize(
    "texto",
    [
        "la app no me deja entrar desde ayer",
        "me cobraron $1.500.000 de más",
        "esperé 45 minutos en la fila del 15 de octubre de 2026",
        "el cajero de la calle 72 # 10-34 no funciona",
        "tasa del 1,5% mensual",
    ],
)
def test_textos_sin_datos_sensibles_no_cambian(texto: str) -> None:
    assert enmascarar(texto) == texto
