"""Cliente HTTP del microservicio de identidad (``contracts/identity-service.openapi.yaml``)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from social_listener.adapters.outbound.resilience import CircuitBreaker
from social_listener.application.dto.identity import IdentityLookup, IdentityLookupResult
from social_listener.application.errors import IdentityServiceUnavailableError
from social_listener.domain.identity.model import IdentityStatus

STATUS = {
    "match": IdentityStatus.IDENTIFICADO,
    "no_match": IdentityStatus.NO_IDENTIFICADO,
    "ambiguous": IdentityStatus.AMBIGUO,
}


class _LookupResponse(BaseModel):
    """Solo los campos del contrato; cualquier otro se descarta sin leerse."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["match", "no_match", "ambiguous"]
    customer_ref: str | None = None
    confidence: float | None = None


@dataclass
class ClientCredentials:
    """OAuth2 client_credentials con caché del token hasta poco antes de que venza."""

    token_url: str
    client_id: str
    client_secret: str
    scope: str = "identity.lookup"
    clock: Callable[[], float] = time.monotonic
    _token: str | None = None
    _expires_at: float = 0.0

    async def token(self, client: httpx.AsyncClient, timeout_s: float) -> str:
        if self._token is not None and self.clock() < self._expires_at:
            return self._token
        response = await client.post(
            self.token_url,
            data={"grant_type": "client_credentials", "scope": self.scope},
            auth=(self.client_id, self.client_secret),
            timeout=timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = str(payload["access_token"])
        self._expires_at = self.clock() + float(payload.get("expires_in", 300)) - 30
        return self._token


class HttpIdentityService:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        credentials: ClientCredentials | None = None,
        timeout_s: float = 2.0,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._credentials = credentials
        self._timeout_s = timeout_s
        self._breaker = breaker or CircuitBreaker(failure_threshold=5, cooldown_s=30)

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    async def lookup(self, query: IdentityLookup) -> IdentityLookupResult:
        if not self._breaker.allow():
            raise IdentityServiceUnavailableError("circuito abierto")
        try:
            response = await self._post(query)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self._breaker.record_failure()
            raise IdentityServiceUnavailableError(type(exc).__name__) from None
        if response.status_code >= 400:
            self._breaker.record_failure()
            raise IdentityServiceUnavailableError(f"HTTP {response.status_code}")
        try:
            parsed = _LookupResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            self._breaker.record_failure()
            raise IdentityServiceUnavailableError("respuesta fuera del contrato") from None
        self._breaker.record_success()
        status = STATUS[parsed.status]
        return IdentityLookupResult(
            status=status,
            customer_ref=parsed.customer_ref if status is IdentityStatus.IDENTIFICADO else None,
            confidence=parsed.confidence,
        )

    async def _post(self, query: IdentityLookup) -> httpx.Response:
        headers = {}
        if self._credentials is not None:
            token = await self._credentials.token(self._client, self._timeout_s)
            headers["Authorization"] = f"Bearer {token}"
        return await self._client.post(
            f"{self._base_url}/v1/identity/lookups",
            json={
                "channel": query.channel,
                "external_author_id": query.external_author_id,
                "username": query.username,
            },
            headers=headers,
            timeout=self._timeout_s,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def mtls_client(cert_file: str, key_file: str, ca_file: str | None = None) -> httpx.AsyncClient:
    """Cliente con TLS mutuo para entornos donde el servicio exige certificado de cliente."""
    import ssl

    context = ssl.create_default_context(cafile=ca_file)
    context.load_cert_chain(cert_file, key_file)
    return httpx.AsyncClient(verify=context)
