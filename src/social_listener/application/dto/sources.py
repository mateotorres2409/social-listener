"""Datos que intercambian los conectores de fuentes con la ingesta."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawMention:
    """Mención tal como la entrega un conector, antes de normalizar.

    Todos los campos son opcionales: la normalización decide si es válida.
    """

    channel: str | None = None
    external_id: str | None = None
    url: str | None = None
    text: str | None = None
    language: str | None = None
    published_at: datetime | None = None
    format: str | None = None
    parent_external_id: str | None = None
    author_external_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    author_followers: int | None = None
    author_verified: bool | None = None
    likes: int | None = None
    shares: int | None = None
    replies: int | None = None
    views: int | None = None
    source_payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorBatch:
    mentions: Sequence[RawMention]
    next_checkpoint: str | None
    has_more: bool = False
