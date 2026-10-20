"""Modelo canónico de Mención y Tópico de escucha."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from social_listener.domain.privacy.model import MaskedText
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import (
    ListeningQueryId,
    MentionId,
    ProjectId,
    require_aware,
)


class Channel(StrEnum):
    """Red social o canal de origen de una mención."""

    X = "x"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    REDDIT = "reddit"
    RESENAS = "resenas"
    MOCK = "mock"


class ContentFormat(StrEnum):
    PUBLICACION = "publicacion"
    COMENTARIO = "comentario"
    RESPUESTA = "respuesta"
    RESENA = "resena"


@dataclass(frozen=True, slots=True)
class AuthorSnapshot:
    """Datos del autor tal como los entrega la fuente en una mención."""

    external_id: str
    username: str
    display_name: str | None = None
    followers: int | None = None
    verified: bool = False

    def __post_init__(self) -> None:
        require(bool(self.external_id.strip()), "el autor requiere id externo")
        require(bool(self.username.strip()), "el autor requiere username")
        require(self.followers is None or self.followers >= 0, "seguidores no puede ser negativo")


ANONYMIZED = "suprimido"
ANONYMIZED_AUTHOR = AuthorSnapshot(external_id=ANONYMIZED, username=ANONYMIZED)
"""Autor con el que quedan las menciones tras la retención o la supresión."""


@dataclass(frozen=True, slots=True)
class Engagement:
    likes: int | None = None
    shares: int | None = None
    replies: int | None = None
    views: int | None = None

    def __post_init__(self) -> None:
        for name in ("likes", "shares", "replies", "views"):
            value = getattr(self, name)
            require(value is None or value >= 0, f"{name} no puede ser negativo")

    @property
    def interactions(self) -> int:
        return sum(v or 0 for v in (self.likes, self.shares, self.replies))


@dataclass(frozen=True, slots=True)
class MentionData:
    """Metadatos canónicos de una mención, independientes de la fuente."""

    channel: Channel
    external_id: str
    url: str
    language: str | None
    published_at: datetime
    author: AuthorSnapshot
    format: ContentFormat = ContentFormat.PUBLICACION
    engagement: Engagement = field(default_factory=Engagement)
    parent_external_id: str | None = None

    def __post_init__(self) -> None:
        require(bool(self.external_id.strip()), "la mención requiere id externo")
        require_aware(self.published_at, "published_at")
        require(
            self.parent_external_id != self.external_id,
            "una mención no puede responderse a sí misma",
        )


@dataclass(frozen=True, slots=True)
class IncomingMention:
    """Mención normalizada que aún conserva el texto original.

    Solo existe entre la ingesta y la etapa de protección de datos.
    """

    data: MentionData
    raw_text: str

    def __post_init__(self) -> None:
        require(bool(self.raw_text.strip()), "la mención requiere texto")


@dataclass(frozen=True, slots=True)
class Mention:
    """Mención persistida. Solo expone el texto enmascarado."""

    id: MentionId
    project_id: ProjectId
    data: MentionData
    query_ids: frozenset[ListeningQueryId]
    text: MaskedText | None = None

    @property
    def is_protected(self) -> bool:
        return self.text is not None


class QueryStatus(StrEnum):
    ACTIVE = "activo"
    PAUSED = "pausado"


@dataclass(frozen=True, slots=True)
class ListeningQuery:
    """Tópico de escucha: qué se escucha, en qué canales y para qué proyecto."""

    id: ListeningQueryId
    project_id: ProjectId
    name: str
    channels: tuple[str, ...]
    terms: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    status: QueryStatus = QueryStatus.ACTIVE

    def __post_init__(self) -> None:
        require(bool(self.name.strip()), "el tópico requiere nombre")
        require(
            any(s.strip() for s in (*self.terms, *self.accounts, *self.hashtags)),
            "el tópico requiere al menos un término, cuenta o hashtag",
        )
        require(any(c.strip() for c in self.channels), "el tópico requiere al menos un canal")

    @property
    def is_active(self) -> bool:
        return self.status is QueryStatus.ACTIVE
