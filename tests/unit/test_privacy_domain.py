import pytest

from social_listener.domain.privacy.model import Detection, SensitiveDataType
from social_listener.domain.privacy.policies import luhn_valid, mask


@pytest.mark.parametrize(
    "numero",
    [
        "4111111111111111",
        "4111 1111 1111 1111",
        "5500-0000-0000-0004",
        "378282246310005",  # American Express, 15 dígitos
        "6011111111111117",
    ],
)
def test_luhn_valido(numero: str) -> None:
    assert luhn_valid(numero)


@pytest.mark.parametrize(
    "numero",
    [
        "4111111111111112",  # dígito verificador incorrecto
        "1234567890123456",
        "411111111111",  # demasiado corto
        "41111111111111111111",  # demasiado largo
        "4111a11111111111",
    ],
)
def test_luhn_invalido(numero: str) -> None:
    assert not luhn_valid(numero)


def test_mask_reemplaza_por_marcadores_tipados() -> None:
    texto = "tarjeta 4111 1111 1111 1111 y correo ana@x.co"
    detecciones = [
        Detection(SensitiveDataType.TARJETA, 8, 27),
        Detection(SensitiveDataType.EMAIL, 37, 45),
    ]

    resultado = mask(texto, detecciones)

    assert resultado.value == "tarjeta [TARJETA] y correo [EMAIL]"
    assert resultado.detected == {SensitiveDataType.TARJETA, SensitiveDataType.EMAIL}
    assert resultado.sensitive_exposure


def test_mask_resuelve_solapamientos() -> None:
    texto = "llamar al 3001234567"
    detecciones = [
        Detection(SensitiveDataType.CUENTA, 10, 20),
        Detection(SensitiveDataType.TELEFONO, 10, 20),
        Detection(SensitiveDataType.DOCUMENTO, 12, 18),
    ]

    resultado = mask(texto, detecciones)

    assert resultado.value.count("[") == 1


def test_mask_sin_detecciones() -> None:
    resultado = mask("sin datos", [])
    assert resultado.value == "sin datos"
    assert not resultado.sensitive_exposure
