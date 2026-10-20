"""Etapas del pipeline, trabajos y cola de errores."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Stage(StrEnum):
    NORMALIZE = "normalizacion"
    PROTECT = "proteccion"
    CLASSIFY = "clasificacion"
    INDIVIDUALIZE = "individualizacion"
    IDENTITY = "identidad"
    EXPORT = "exportacion"
    IDENTITY_RETRY = "identidad_reintento"


@dataclass(frozen=True, slots=True)
class Job:
    """Trabajo de una etapa sobre un sujeto (mención o autor)."""

    id: str
    stage: Stage
    subject_id: str
    attempts: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeadLetter:
    id: str
    stage: Stage
    reason: str
    attempts: int
    created_at: datetime
    subject_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
