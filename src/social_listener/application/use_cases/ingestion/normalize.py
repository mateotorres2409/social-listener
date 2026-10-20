"""Normalización de menciones crudas al modelo canónico."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from social_listener.application.dto.sources import RawMention
from social_listener.domain.mentions.model import (
    AuthorSnapshot,
    Channel,
    ContentFormat,
    Engagement,
    IncomingMention,
    MentionData,
)
from social_listener.domain.shared.errors import DomainError


@dataclass(frozen=True, slots=True)
class InvalidMention:
    reasons: tuple[str, ...]
    raw: RawMention


def normalize(
    raw: RawMention, default_channel: str | None = None
) -> IncomingMention | InvalidMention:
    reasons: list[str] = []
    if not (raw.external_id or "").strip():
        reasons.append("sin id externo")
    if not (raw.text or "").strip():
        reasons.append("sin texto")
    if not (raw.author_external_id or "").strip() or not (raw.author_username or "").strip():
        reasons.append("sin autor")
    if raw.published_at is None or raw.published_at.tzinfo is None:
        reasons.append("sin fecha de publicación con zona horaria")
    if not (raw.url or "").strip():
        reasons.append("sin url")
    channel_name = raw.channel or default_channel
    channel = _parse(Channel, channel_name)
    if channel is None:
        reasons.append(f"canal desconocido: {channel_name!r}")
    content_format = _parse(ContentFormat, raw.format) if raw.format else None
    if raw.format and content_format is None:
        reasons.append(f"formato desconocido: {raw.format!r}")
    if reasons:
        return InvalidMention(tuple(reasons), raw)

    if channel is None or raw.published_at is None:  # pragma: no cover - validado arriba
        return InvalidMention(("datos incompletos",), raw)
    if content_format is None:
        content_format = (
            ContentFormat.RESPUESTA if raw.parent_external_id else ContentFormat.PUBLICACION
        )
    try:
        return IncomingMention(
            data=MentionData(
                channel=channel,
                external_id=(raw.external_id or "").strip(),
                url=(raw.url or "").strip(),
                language=(raw.language or None),
                published_at=raw.published_at,
                author=AuthorSnapshot(
                    external_id=(raw.author_external_id or "").strip(),
                    username=(raw.author_username or "").strip().lstrip("@"),
                    display_name=raw.author_display_name,
                    followers=raw.author_followers,
                    verified=bool(raw.author_verified),
                ),
                format=content_format,
                engagement=Engagement(
                    likes=raw.likes, shares=raw.shares, replies=raw.replies, views=raw.views
                ),
                parent_external_id=raw.parent_external_id or None,
            ),
            raw_text=raw.text or "",
        )
    except DomainError as exc:
        return InvalidMention((str(exc),), raw)


def _parse[E: (Channel, ContentFormat)](enum: type[E], value: str | None) -> E | None:
    if value is None:
        return None
    try:
        return enum(value.strip().lower())
    except ValueError:
        return None


def raw_to_payload(raw: RawMention) -> dict[str, Any]:
    """Serializa una mención cruda para la cola de errores (sin ``source_payload``)."""
    data = asdict(raw)
    data.pop("source_payload", None)
    if isinstance(data.get("published_at"), datetime):
        data["published_at"] = data["published_at"].isoformat()
    return data


def raw_from_payload(data: dict[str, Any]) -> RawMention:
    values = dict(data)
    if isinstance(values.get("published_at"), str):
        values["published_at"] = datetime.fromisoformat(values["published_at"])
    return RawMention(**values)
