"""Consulta de menciones y su clasificación (solo texto enmascarado)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from social_listener.application.errors import NotFoundError
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.domain.authors.model import Author
from social_listener.domain.classification.model import MentionClassification
from social_listener.domain.mentions.model import Mention
from social_listener.domain.shared.types import AuthorId, MentionId


@dataclass(frozen=True, slots=True)
class MentionView:
    mention: Mention
    classification: MentionClassification | None


class ViewMentions:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get(self, mention_id: MentionId) -> MentionView:
        async with self._uow_factory() as uow:
            mention = await uow.mentions.get(mention_id)
            if mention is None:
                raise NotFoundError(f"la mención {mention_id} no existe")
            return MentionView(mention, await uow.classifications.get(mention_id))


@dataclass(frozen=True, slots=True)
class AuthorView:
    author: Author
    mentions: Sequence[MentionView]


class ViewAuthors:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get(self, author_id: AuthorId) -> AuthorView:
        """Perfil e historial del autor, con las menciones ordenadas por fecha."""
        async with self._uow_factory() as uow:
            author = await uow.authors.get(author_id)
            if author is None:
                raise NotFoundError(f"el autor {author_id} no existe")
            mentions = await uow.mentions.list_by_author(author_id)
            views = [MentionView(m, await uow.classifications.get(m.id)) for m in mentions]
        return AuthorView(author, views)
