"""Configuración de la plataforma leída del entorno.

Los secretos se declaran como ``SecretStr`` y nunca tienen valor por defecto: si
faltan, el arranque falla con un error que nombra las variables sin mostrar
ningún valor recibido.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "SL_"


class ConfigurationError(RuntimeError):
    """La configuración del entorno es inválida o está incompleta."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"

    # Secretos obligatorios (sin valor por defecto)
    database_url: SecretStr
    pseudonymization_key: SecretStr
    pseudonymization_key_id: str = Field(min_length=1)
    api_token: SecretStr

    # Microservicio de identidad
    identity_service_url: str = "http://localhost:8081"
    identity_timeout_s: float = Field(default=2.0, gt=0)
    identity_client_id: str | None = None
    identity_client_secret: SecretStr | None = None
    identity_token_url: str | None = None
    identity_client_cert: str | None = None
    identity_client_key: str | None = None
    identity_ca_file: str | None = None

    # Pipeline
    stage_max_attempts: int = Field(default=5, ge=1)
    stage_batch_size: int = Field(default=50, ge=1)

    # Protección de datos
    retention_interval_s: float = Field(default=86_400.0, gt=0)

    # Ingesta
    ingest_interval_s: float = Field(default=60.0, gt=0)
    enable_mock_connector: bool = True
    fixture_path: Path | None = None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Carga la configuración del entorno (o de ``environ`` en pruebas).

    Convierte los errores de validación en ``ConfigurationError`` sin incluir los
    valores recibidos, que pueden contener secretos.
    """
    try:
        if environ is None:
            return Settings()  # type: ignore[call-arg]
        values = {
            key[len(ENV_PREFIX) :].lower(): value
            for key, value in environ.items()
            if key.startswith(ENV_PREFIX)
        }
        return Settings.model_validate(values)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"])
            variable = f"{ENV_PREFIX}{field.upper()}"
            reason = "es obligatoria" if error["type"] == "missing" else "es inválida"
            problems.append(f"{variable} {reason}")
        raise ConfigurationError("Configuración inválida: " + "; ".join(sorted(problems))) from None
