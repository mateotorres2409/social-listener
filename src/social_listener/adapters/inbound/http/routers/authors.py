from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.routers.classification import MentionOut
from social_listener.adapters.inbound.http.schemas import ApiModel
from social_listener.application.use_cases.mentions import AuthorView
from social_listener.domain.shared.types import AuthorId

router = APIRouter(tags=["autores"])


class UsernameChangeOut(ApiModel):
    username: str
    replaced_at: datetime


class AuthorOut(ApiModel):
    id: str
    project_id: str
    channel: str
    external_id: str
    username: str
    display_name: str | None
    followers: int | None
    verified: bool
    first_seen_at: datetime
    last_seen_at: datetime
    total_mentions: int
    type_counts: dict[str, int]
    sentiment_trend: float | None
    recurrent: bool
    is_official: bool
    previous_usernames: list[UsernameChangeOut]
    mentions: list[MentionOut]

    @classmethod
    def from_view(cls, view: AuthorView) -> AuthorOut:
        a = view.author
        return cls(
            id=a.id,
            project_id=a.project_id,
            channel=a.channel.value,
            external_id=a.external_id,
            username=a.username,
            display_name=a.display_name,
            followers=a.followers,
            verified=a.verified,
            first_seen_at=a.first_seen_at,
            last_seen_at=a.last_seen_at,
            total_mentions=a.total_mentions,
            type_counts=dict(a.type_counts),
            sentiment_trend=a.sentiment_trend,
            recurrent=a.recurrent,
            is_official=a.is_official,
            previous_usernames=[
                UsernameChangeOut(username=c.username, replaced_at=c.replaced_at)
                for c in a.previous_usernames
            ],
            mentions=[MentionOut.from_view(m) for m in view.mentions],
        )


@router.get("/authors/{author_id}")
async def get_author(author_id: str, deps: Deps) -> AuthorOut:
    return AuthorOut.from_view(await deps.authors.get(AuthorId(author_id)))


@router.post("/authors/{author_id}/identity/revalidate", status_code=status.HTTP_202_ACCEPTED)
async def revalidate_identity(author_id: str, deps: Deps) -> dict[str, str]:
    await deps.revalidate_identity.execute(AuthorId(author_id))
    return {"status": "pendiente"}
