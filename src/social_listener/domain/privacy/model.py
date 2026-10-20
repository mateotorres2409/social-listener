"""Tipos de datos sensibles y texto enmascarado."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from social_listener.domain.shared.errors import require


class SensitiveDataType(StrEnum):
    TARJETA = "tarjeta"
    CUENTA = "cuenta"
    DOCUMENTO = "documento"
    TELEFONO = "telefono"
    EMAIL = "email"

    @property
    def marker(self) -> str:
        return f"[{self.name}]"


@dataclass(frozen=True, slots=True)
class Detection:
    """Un dato sensible encontrado en ``text[start:end]``."""

    kind: SensitiveDataType
    start: int
    end: int

    def __post_init__(self) -> None:
        require(0 <= self.start < self.end, "rango de detección inválido")


@dataclass(frozen=True, slots=True)
class MaskedText:
    """Texto con los datos sensibles reemplazados por marcadores tipados."""

    value: str
    detected: frozenset[SensitiveDataType] = frozenset()

    def __post_init__(self) -> None:
        for kind in self.detected:
            require(kind.marker in self.value, f"falta el marcador {kind.marker} en el texto")

    @property
    def sensitive_exposure(self) -> bool:
        """Indica si el autor expuso datos sensibles en la mención."""
        return bool(self.detected)
