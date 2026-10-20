from __future__ import annotations

from fastapi import APIRouter, Response, status

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.schemas import ListeningQueryIn, ListeningQueryOut
from social_listener.domain.mentions.model import QueryStatus
from social_listener.domain.shared.types import ListeningQueryId, ProjectId

router = APIRouter(tags=["tópicos de escucha"])


@router.post("/projects/{project_id}/listening-queries", status_code=status.HTTP_201_CREATED)
async def create_query(project_id: str, body: ListeningQueryIn, deps: Deps) -> ListeningQueryOut:
    query = await deps.queries.create(ProjectId(project_id), body.to_input())
    return ListeningQueryOut.from_domain(query)


@router.get("/projects/{project_id}/listening-queries")
async def list_queries(project_id: str, deps: Deps) -> list[ListeningQueryOut]:
    queries = await deps.queries.list_by_project(ProjectId(project_id))
    return [ListeningQueryOut.from_domain(q) for q in queries]


@router.get("/listening-queries/{query_id}")
async def get_query(query_id: str, deps: Deps) -> ListeningQueryOut:
    return ListeningQueryOut.from_domain(await deps.queries.get(ListeningQueryId(query_id)))


@router.put("/listening-queries/{query_id}")
async def update_query(query_id: str, body: ListeningQueryIn, deps: Deps) -> ListeningQueryOut:
    query = await deps.queries.update(ListeningQueryId(query_id), body.to_input())
    return ListeningQueryOut.from_domain(query)


@router.post("/listening-queries/{query_id}/pause")
async def pause_query(query_id: str, deps: Deps) -> ListeningQueryOut:
    query = await deps.queries.set_status(ListeningQueryId(query_id), QueryStatus.PAUSED)
    return ListeningQueryOut.from_domain(query)


@router.post("/listening-queries/{query_id}/resume")
async def resume_query(query_id: str, deps: Deps) -> ListeningQueryOut:
    query = await deps.queries.set_status(ListeningQueryId(query_id), QueryStatus.ACTIVE)
    return ListeningQueryOut.from_domain(query)


@router.delete("/listening-queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_query(query_id: str, deps: Deps) -> Response:
    await deps.queries.delete(ListeningQueryId(query_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
