"""Gestión de proyectos (unidad multi-tenant y su configuración de negocio)."""

from __future__ import annotations

from collections.abc import Sequence

from social_listener.application.errors import NotFoundError
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.types import ProjectId


class ManageProjects:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def save(self, project: Project) -> Project:
        async with self._uow_factory() as uow:
            await uow.projects.save(project)
            await uow.commit()
        return project

    async def get(self, project_id: ProjectId) -> Project:
        async with self._uow_factory() as uow:
            project = await uow.projects.get(project_id)
        if project is None:
            raise NotFoundError(f"proyecto {project_id} no existe")
        return project

    async def list_all(self) -> Sequence[Project]:
        async with self._uow_factory() as uow:
            return await uow.projects.list_all()
