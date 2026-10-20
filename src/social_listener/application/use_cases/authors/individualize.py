"""Etapa de individualización: autor por canal, perfil, recurrencia e hilos."""

from __future__ import annotations

from dataclasses import replace

from social_listener.application.dto.pipeline import Job
from social_listener.application.ports.inbound.stages import (
    ADVANCE,
    PermanentStageError,
    StageOutcome,
)
from social_listener.application.ports.outbound.services import Clock
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.domain.authors.model import Author, Thread
from social_listener.domain.authors.policies import (
    is_recurrent,
    recurrence_window_start,
    register_mention,
)
from social_listener.domain.classification.model import Dimension
from social_listener.domain.mentions.model import Mention
from social_listener.domain.projects.model import RecurrenceRule
from social_listener.domain.shared.types import AuthorId, MentionId, ThreadId, new_id


class IndividualizeAuthor:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        mention = await uow.mentions.get(MentionId(job.subject_id))
        if mention is None:
            raise PermanentStageError("la mención no existe")
        project = await uow.projects.get(mention.project_id)
        if project is None:
            raise PermanentStageError("el proyecto de la mención no existe")
        classification = await uow.classifications.get(mention.id)
        data = mention.data
        is_official = project.is_official(data.channel, data.author.external_id)

        current = await uow.authors.get_by_external(
            project.id, data.channel, data.author.external_id
        )
        author = register_mention(
            current,
            new_id=AuthorId(new_id()),
            project_id=project.id,
            data=data,
            mention_type=classification.value_of(Dimension.TIPO) if classification else None,
            sentiment_score=classification.sentiment_score if classification else None,
            is_official=is_official,
        )
        await uow.authors.save(author)

        thread = await self._thread(uow, mention)
        if is_official and not thread.attended_by_entity:
            thread = replace(thread, attended_by_entity=True)
            await uow.threads.save(thread)
        await uow.mentions.assign_author(mention.id, author.id, thread.id)

        await self._update_recurrence(uow, author, project.recurrence)
        return ADVANCE

    async def _thread(self, uow: UnitOfWork, mention: Mention) -> Thread:
        data = mention.data
        own_thread = await uow.threads.get_by_root(
            mention.project_id, data.channel, data.external_id
        )

        if data.parent_external_id is None:
            # Publicación raíz: si sus respuestas llegaron antes, el hilo ya existe.
            return own_thread or await self._new_thread(uow, mention, data.external_id)

        thread = await self._parent_thread(uow, mention)
        if own_thread is not None and own_thread.id != thread.id:
            # Llegaron respuestas a esta mención antes que ella: se unen a su hilo.
            await uow.mentions.reassign_thread(own_thread.id, thread.id)
            await uow.threads.delete(own_thread.id)
            if own_thread.attended_by_entity and not thread.attended_by_entity:
                thread = replace(thread, attended_by_entity=True)
                await uow.threads.save(thread)
        return thread

    async def _parent_thread(self, uow: UnitOfWork, mention: Mention) -> Thread:
        data = mention.data
        assert data.parent_external_id is not None  # noqa: S101
        parent = await uow.mentions.find_by_external(data.channel, data.parent_external_id)
        if parent is not None:
            parent_thread_id = await uow.mentions.thread_of(parent.id)
            if parent_thread_id is not None:
                found = await uow.threads.get(parent_thread_id)
                if found is not None:
                    return found
        # El padre aún no llega (o no se individualizó): el hilo se ancla en él.
        existing = await uow.threads.get_by_root(
            mention.project_id, data.channel, data.parent_external_id
        )
        return existing or await self._new_thread(uow, mention, data.parent_external_id)

    @staticmethod
    async def _new_thread(uow: UnitOfWork, mention: Mention, root: str) -> Thread:
        thread = Thread(ThreadId(new_id()), mention.project_id, mention.data.channel, root)
        await uow.threads.save(thread)
        return thread

    async def _update_recurrence(
        self, uow: UnitOfWork, author: Author, rule: RecurrenceRule
    ) -> None:
        since = recurrence_window_start(self._clock.now(), rule)
        recurrent = is_recurrent(await uow.authors.complaints_since(author.id, since), rule)
        if recurrent != author.recurrent:
            await uow.authors.save(replace(author, recurrent=recurrent))
