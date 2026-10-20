"""Proveedores de secretos.

``SecretProvider`` es interno a los adaptadores: los casos de uso nunca ven un
secreto. En producción se reemplaza por el gestor de secretos de la entidad
(Vault, AWS Secrets Manager, Azure Key Vault) implementando la misma interfaz.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol


class SecretNotFoundError(KeyError):
    """El secreto no existe. El mensaje solo nombra la referencia, nunca un valor."""


class SecretProvider(Protocol):
    def get(self, ref: str) -> str: ...


class StaticSecretProvider:
    def __init__(self, secrets: Mapping[str, str]) -> None:
        self._secrets = dict(secrets)

    def get(self, ref: str) -> str:
        try:
            return self._secrets[ref]
        except KeyError:
            raise SecretNotFoundError(f"secreto no encontrado: {ref}") from None


class LayeredSecretProvider:
    """Busca el secreto en cada proveedor, en orden."""

    def __init__(self, *providers: SecretProvider) -> None:
        self._providers = providers

    def get(self, ref: str) -> str:
        for provider in self._providers:
            try:
                return provider.get(ref)
            except SecretNotFoundError:
                continue
        raise SecretNotFoundError(f"secreto no encontrado: {ref}")


class EnvSecretProvider:
    """Lee ``SL_SECRET_<REF>`` del entorno (referencia en mayúsculas, ``-`` → ``_``)."""

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def get(self, ref: str) -> str:
        name = "SL_SECRET_" + ref.upper().replace("-", "_").replace(".", "_")
        value = self._environ.get(name)
        if not value:
            raise SecretNotFoundError(f"secreto no encontrado: {ref} ({name})")
        return value
