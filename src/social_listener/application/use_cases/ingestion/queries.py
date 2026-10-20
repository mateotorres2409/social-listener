"""Gestión de tópicos de escucha."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from social_listener.application.errors import NotFoundError
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.domain.mentions.model import ListeningQuery, QueryStatus
from social_listener.domain.shared.errors import require
from social_listener.domain.shared.types import ListeningQueryId, ProjectId, new_id


@dataclass(frozen=True, slots=True)
class ListeningQueryInput:
    name: str
    channels: tuple[str, ...]
    terms: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


class ManageListeningQueries:
    def __init__(self, uow_factory: UnitOfWorkFactory, known_connectors: Collection[str]) -> None:
        self._uow_factory = uow_factory
        self._known = frozenset(known_connectors)

    def _validate_channels(self, channels: Sequence[str]) -> None:
        unknown = sorted({c for c in channels if c not in self._known})
        require(not unknown, f"canales sin conector: {', '.join(unknown)}")

    async def create(self, project_id: ProjectId, data: ListeningQueryInput) -> ListeningQuery:
        self._validate_channels(data.channels)
        query = ListeningQuery(
            id=ListeningQueryId(new_id()),
            project_id=project_id,
            name=data.name,
            channels=data.channels,
            terms=data.terms,
            accounts=data.accounts,
            hashtags=data.hashtags,
            languages=data.languages,
        )
        async with self._uow_factory() as uow:
            if await uow.projects.get(project_id) is None:
                raise NotFoundError(f"proyecto {project_id} no existe")
            await uow.queries.add(query)
            await uow.commit()
        return query

    async def get(self, query_id: ListeningQueryId) -> ListeningQuery:
        async with self._uow_factory() as uow:
            query = await uow.queries.get(query_id)
        if query is None:
            raise NotFoundError(f"tópico {query_id} no existe")
        return query

    async def list_by_project(self, project_id: ProjectId) -> Sequence[ListeningQuery]:
        async with self._uow_factory() as uow:
            return await uow.queries.list_by_project(project_id)

    async def update(self, query_id: ListeningQueryId, data: ListeningQueryInput) -> ListeningQuery:
        self._validate_channels(data.channels)
        current = await self.get(query_id)
        updated = replace(
            current,
            name=data.name,
            channels=data.channels,
            terms=data.terms,
            accounts=data.accounts,
            hashtags=data.hashtags,
            languages=data.languages,
        )
        return await self._save(updated)

    async def set_status(self, query_id: ListeningQueryId, status: QueryStatus) -> ListeningQuery:
        return await self._save(replace(await self.get(query_id), status=status))

    async def delete(self, query_id: ListeningQueryId) -> None:
        async with self._uow_factory() as uow:
            if not await uow.queries.delete(query_id):
                raise NotFoundError(f"tópico {query_id} no existe")
            await uow.commit()

    async def _save(self, query: ListeningQuery) -> ListeningQuery:
        async with self._uow_factory() as uow:
            await uow.queries.save(query)
            await uow.commit()
        return query
