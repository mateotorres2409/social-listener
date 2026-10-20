"""Stub del microservicio de identidad para desarrollo y pruebas.

Responde según el prefijo del username:

- ``cliente_*``  → ``match`` con una referencia opaca determinista
- ``ambiguo_*``  → ``ambiguous``
- ``lento_*``    → espera ``STUB_SLOW_SECONDS`` (5 s por defecto) y responde ``no_match``
- ``error_*``    → HTTP 503
- cualquier otro → ``no_match``

Si ``STUB_TOKEN`` está definido, exige ``Authorization: Bearer <STUB_TOKEN>``.
Agrega un campo fuera del contrato (``extra``) para comprobar que el cliente lo descarta.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1)
    external_author_id: str = Field(min_length=1)
    username: str = Field(min_length=1)


def create_app(*, token: str | None = None, slow_seconds: float | None = None) -> FastAPI:
    expected = token if token is not None else os.environ.get("STUB_TOKEN")
    delay = (
        slow_seconds
        if slow_seconds is not None
        else float(os.environ.get("STUB_SLOW_SECONDS", "5"))
    )
    app = FastAPI(title="Stub de identidad")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/identity/lookups")
    async def lookup(
        body: LookupRequest, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, Any]:
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="credenciales inválidas")
        username = body.username.lower()
        extra = {"nombre_completo": "Dato que el cliente debe descartar"}
        if username.startswith("error_"):
            raise HTTPException(status_code=503, detail="no disponible")
        if username.startswith("lento_"):
            await asyncio.sleep(delay)
        if username.startswith("cliente_"):
            digest = hashlib.sha256(f"{body.channel}:{body.external_author_id}".encode())
            ref = "c-" + digest.hexdigest()[:12]
            return {"status": "match", "customer_ref": ref, "confidence": 0.94, "extra": extra}
        if username.startswith("ambiguo_"):
            return {"status": "ambiguous", "customer_ref": None, "confidence": 0.5, "extra": extra}
        return {"status": "no_match", "customer_ref": None, "confidence": 0.0, "extra": extra}

    return app


app = create_app()
