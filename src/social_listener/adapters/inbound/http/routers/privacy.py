from __future__ import annotations

from fastapi import APIRouter

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.schemas import ApiModel
from social_listener.domain.mentions.model import Channel
from social_listener.domain.shared.types import ProjectId

router = APIRouter(tags=["protección de datos"])


class SuppressionIn(ApiModel):
    channel: Channel
    external_id: str
    requested_by: str = "api"


class SuppressionOut(ApiModel):
    found: bool
    mentions_anonymized: int


@router.post("/projects/{project_id}/authors/suppress")
async def suppress_author(project_id: str, body: SuppressionIn, deps: Deps) -> SuppressionOut:
    result = await deps.suppress_author.execute(
        ProjectId(project_id), body.channel, body.external_id, actor=body.requested_by
    )
    return SuppressionOut(found=result.found, mentions_anonymized=result.mentions_anonymized)
