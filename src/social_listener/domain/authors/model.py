"""Autores individualizados por canal e hilos de conversación."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from social_listener.domain.mentions.model import Channel
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import AuthorId, ProjectId, ThreadId, require_aware


@dataclass(frozen=True, slots=True)
class UsernameChange:
    username: str
    replaced_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.replaced_at, "replaced_at")


@dataclass(frozen=True, slots=True)
class Author:
    """Perfil de un autor dentro de un canal y un proyecto."""

    id: AuthorId
    project_id: ProjectId
    channel: Channel
    external_id: str
    username: str
    first_seen_at: datetime
    last_seen_at: datetime
    display_name: str | None = None
    followers: int | None = None
    verified: bool = False
    total_mentions: int = 0
    type_counts: Mapping[str, int] = field(default_factory=dict)
    sentiment_trend: float | None = None
    recurrent: bool = False
    is_official: bool = False
    previous_usernames: tuple[UsernameChange, ...] = ()

    def __post_init__(self) -> None:
        require(bool(self.external_id.strip()), "el autor requiere id externo")
        require(bool(self.username.strip()), "el autor requiere username")
        require_aware(self.first_seen_at, "first_seen_at")
        require_aware(self.last_seen_at, "last_seen_at")
        require(self.first_seen_at <= self.last_seen_at, "first_seen_at debe ser <= last_seen_at")
        require(self.total_mentions >= 0, "total_mentions no puede ser negativo")
        require(
            self.sentiment_trend is None or -1.0 <= self.sentiment_trend <= 1.0,
            "sentiment_trend debe estar entre -1 y 1",
        )


@dataclass(frozen=True, slots=True)
class Thread:
    """Conversación: menciones relacionadas por respuesta dentro de un canal."""

    id: ThreadId
    project_id: ProjectId
    channel: Channel
    root_external_id: str
    attended_by_entity: bool = False

    def __post_init__(self) -> None:
        require(bool(self.root_external_id.strip()), "el hilo requiere la mención raíz")
