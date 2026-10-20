"""Políticas puras de individualización: perfil, recurrencia e hilos."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from social_listener.domain.authors.model import Author, UsernameChange
from social_listener.domain.mentions.model import MentionData
from social_listener.domain.projects.model import RecurrenceRule
from social_listener.domain.shared.types import AuthorId, ProjectId

SENTIMENT_RECENCY_WEIGHT = 0.3
"""Peso de la mención más reciente en el sentimiento acumulado (media móvil exponencial)."""


def accumulate_sentiment(previous: float | None, score: float | None) -> float | None:
    if score is None:
        return previous
    if previous is None:
        return round(score, 4)
    blended = SENTIMENT_RECENCY_WEIGHT * score + (1 - SENTIMENT_RECENCY_WEIGHT) * previous
    return round(max(-1.0, min(1.0, blended)), 4)


def register_mention(
    author: Author | None,
    *,
    new_id: AuthorId,
    project_id: ProjectId,
    data: MentionData,
    mention_type: str | None,
    sentiment_score: float | None,
    is_official: bool,
) -> Author:
    """Crea o actualiza el perfil del autor con una mención nueva."""
    snapshot = data.author
    seen = data.published_at
    type_counts = dict(author.type_counts) if author else {}
    if mention_type:
        type_counts[mention_type] = type_counts.get(mention_type, 0) + 1

    if author is None:
        return Author(
            id=new_id,
            project_id=project_id,
            channel=data.channel,
            external_id=snapshot.external_id,
            username=snapshot.username,
            first_seen_at=seen,
            last_seen_at=seen,
            display_name=snapshot.display_name,
            followers=snapshot.followers,
            verified=snapshot.verified,
            total_mentions=1,
            type_counts=type_counts,
            sentiment_trend=accumulate_sentiment(None, sentiment_score),
            is_official=is_official,
        )

    history = author.previous_usernames
    if snapshot.username != author.username:
        history = (*history, UsernameChange(author.username, seen))
    newer = seen >= author.last_seen_at
    return replace(
        author,
        username=snapshot.username if newer else author.username,
        previous_usernames=history if newer else author.previous_usernames,
        display_name=(snapshot.display_name or author.display_name)
        if newer
        else author.display_name,
        followers=snapshot.followers
        if newer and snapshot.followers is not None
        else author.followers,
        verified=snapshot.verified if newer else author.verified,
        first_seen_at=min(author.first_seen_at, seen),
        last_seen_at=max(author.last_seen_at, seen),
        total_mentions=author.total_mentions + 1,
        type_counts=type_counts,
        sentiment_trend=accumulate_sentiment(author.sentiment_trend, sentiment_score),
        is_official=author.is_official or is_official,
    )


def username_changed(author: Author | None, data: MentionData) -> bool:
    return author is not None and author.username != data.author.username


def is_recurrent(complaints_in_window: int, rule: RecurrenceRule) -> bool:
    """Recurrente si supera el umbral de quejas o reclamos dentro de la ventana."""
    return complaints_in_window > rule.threshold


def recurrence_window_start(now: datetime, rule: RecurrenceRule) -> datetime:
    return now - timedelta(days=rule.window_days)


def thread_root_candidate(data: MentionData) -> str:
    """Raíz provisional del hilo: el padre si lo tiene; si no, la propia mención."""
    return data.parent_external_id or data.external_id
