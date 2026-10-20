from __future__ import annotations

from fastapi import APIRouter, status

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.schemas import ProjectIn, ProjectOut
from social_listener.domain.shared.types import ProjectId, new_id

router = APIRouter(tags=["proyectos"])


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectIn, deps: Deps) -> ProjectOut:
    project = await deps.projects.save(body.to_domain(new_id()))
    return ProjectOut.from_domain(project)


@router.get("/projects")
async def list_projects(deps: Deps) -> list[ProjectOut]:
    return [ProjectOut.from_domain(p) for p in await deps.projects.list_all()]


@router.get("/projects/{project_id}")
async def get_project(project_id: str, deps: Deps) -> ProjectOut:
    return ProjectOut.from_domain(await deps.projects.get(ProjectId(project_id)))


@router.put("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectIn, deps: Deps) -> ProjectOut:
    await deps.projects.get(ProjectId(project_id))
    project = await deps.projects.save(body.to_domain(project_id))
    return ProjectOut.from_domain(project)
