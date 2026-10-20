"""Errores de la capa de aplicación que los adaptadores traducen desde su tecnología."""

from __future__ import annotations


class ApplicationError(Exception):
    """Error base de la capa de aplicación."""


class NotFoundError(ApplicationError):
    """El recurso solicitado no existe."""


class SourceError(ApplicationError):
    """Error no transitorio de una fuente (p. ej. credenciales rechazadas)."""


class TransientSourceError(ApplicationError):
    """Error transitorio de una fuente; la operación puede reintentarse."""


class RateLimitedError(TransientSourceError):
    """La fuente aplicó un límite de tasa."""

    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__("límite de tasa de la fuente")
        self.retry_after_s = retry_after_s


class ModelUnavailableError(ApplicationError):
    """Ningún modelo de lenguaje pudo responder (error, timeout o desactivado)."""


class InvalidModelOutputError(ApplicationError):
    """El modelo respondió algo que no cumple el esquema de salida."""


class IdentityServiceUnavailableError(ApplicationError):
    """El microservicio de identidad no respondió a tiempo o falló."""
