"""Errores del dominio."""

from __future__ import annotations


class DomainError(Exception):
    """Error base del dominio."""


class InvalidDomainValueError(DomainError, ValueError):
    """Un valor no cumple las invariantes de una entidad o value object."""


def require(condition: bool, message: str) -> None:
    """Valida una invariante del dominio."""
    if not condition:
        raise InvalidDomainValueError(message)
