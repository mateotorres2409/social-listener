from __future__ import annotations

from fastapi import APIRouter, Query

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.schemas import DeadLetterOut, ReprocessOut

router = APIRouter(tags=["cola de errores"])


@router.get("/dead-letters")
async def list_dead_letters(
    deps: Deps, limit: int = Query(default=100, ge=1, le=1000)
) -> list[DeadLetterOut]:
    return [DeadLetterOut.from_domain(d) for d in await deps.list_dead_letters.execute(limit)]


@router.post("/dead-letters/{letter_id}/reprocess")
async def reprocess_dead_letter(letter_id: str, deps: Deps) -> ReprocessOut:
    result = await deps.reprocess_dead_letter.execute(letter_id)
    return ReprocessOut(stage=result.stage.value, accepted=result.accepted, reason=result.reason)
