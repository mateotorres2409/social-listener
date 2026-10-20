"""Seudonimización con HMAC-SHA256 y clave rotable."""

from __future__ import annotations

import hashlib
import hmac

from social_listener.adapters.outbound.secrets.provider import SecretProvider


class HmacPseudonymizer:
    """Seudónimo estable ``<key_id>:<32 hex>``; irreversible sin la clave.

    El ``key_id`` viaja en el seudónimo para saber con qué clave se generó y poder
    rotarla: tras la rotación, los consumidores verán seudónimos nuevos.
    """

    def __init__(self, secrets: SecretProvider, *, key_ref: str, key_id: str) -> None:
        key = secrets.get(key_ref).encode()
        if len(key) < 16:
            raise ValueError("la clave de seudonimización debe tener al menos 16 bytes")
        self._key = key
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def pseudonymize(self, namespace: str, value: str) -> str:
        message = f"{namespace}\x1f{value}".encode()
        digest = hmac.new(self._key, message, hashlib.sha256).hexdigest()
        return f"{self._key_id}:{digest[:32]}"
