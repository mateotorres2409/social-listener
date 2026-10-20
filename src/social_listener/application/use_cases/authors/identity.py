"""Validación de identidad de autores contra el microservicio de la entidad."""

from __future__ import annotations

from social_listener.application.backoff import Backoff
from social_listener.application.dto.export import EventName, IdentityResolved, OutboxEvent
from social_listener.application.dto.identity import IdentityLookup, IdentityLookupResult
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.errors import IdentityServiceUnavailableError, NotFoundError
from social_listener.application.ports.inbound.stages import ADVANCE, STOP, StageOutcome
from social_listener.application.ports.outbound.services import (
    Clock,
    IdentityService,
    Pseudonymizer,
)
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork, UnitOfWorkFactory
from social_listener.application.use_cases.export.assemble import (
    author_pseudonym_of,
    customer_pseudonym_of,
)
from social_listener.domain.authors.model import Author
from social_listener.domain.identity.model import IdentityResult, IdentityStatus
from social_listener.domain.identity.policies import IdentityTtlPolicy
from social_listener.domain.shared.types import AuthorId, MentionId


class _IdentityResolver:
    def __init__(
        self,
        identity: IdentityService,
        pseudonymizer: Pseudonymizer,
        clock: Clock,
    ) -> None:
        self._identity = identity
        self._pseudonymizer = pseudonymizer
        self._clock = clock

    async def lookup(self, author: Author) -> IdentityLookupResult:
        return await self._identity.lookup(
            IdentityLookup(author.channel.value, author.external_id, author.username)
        )

    async def save_definitive(
        self,
        uow: UnitOfWork,
        author: Author,
        found: IdentityLookupResult,
        previous: IdentityResult | None,
    ) -> IdentityResult:
        result = IdentityResult(
            author_id=author.id,
            status=found.status,
            validated_at=self._clock.now(),
            username=author.username,
            customer_ref=found.customer_ref
            if found.status is IdentityStatus.IDENTIFICADO
            else None,
            confidence=found.confidence,
        )
        await uow.identity_results.save(result)
        if previous is not None and not previous.status.is_definitive:
            await self._emit_resolved(uow, author, result)
        return result

    async def _emit_resolved(self, uow: UnitOfWork, author: Author, result: IdentityResult) -> None:
        customer = (
            customer_pseudonym_of(self._pseudonymizer, result.customer_ref)
            if result.customer_ref
            else None
        )
        await uow.outbox.add(
            OutboxEvent(
                name=EventName.AUTHOR_IDENTITY_RESOLVED,
                occurred_at=self._clock.now(),
                identity=IdentityResolved(
                    author_pseudonym_id=author_pseudonym_of(
                        self._pseudonymizer, author.channel.value, author.external_id
                    ),
                    project_id=author.project_id,
                    identity_status=result.status,
                    customer_pseudonym=customer,
                ),
            )
        )


class ValidateIdentity:
    """Etapa ``identidad``: nunca bloquea el pipeline."""

    def __init__(
        self,
        identity: IdentityService,
        pseudonymizer: Pseudonymizer,
        clock: Clock,
        *,
        ttl: IdentityTtlPolicy | None = None,
        retry_backoff: Backoff | None = None,
    ) -> None:
        self._resolver = _IdentityResolver(identity, pseudonymizer, clock)
        self._clock = clock
        self._ttl = ttl or IdentityTtlPolicy()
        self._backoff = retry_backoff or Backoff(base_s=30, max_s=3600)

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        author_id = await uow.mentions.author_of(MentionId(job.subject_id))
        if author_id is None:
            return ADVANCE  # sin autor (p. ej. suprimido): se exporta como pendiente
        author = await uow.authors.get(author_id)
        if author is None:
            return ADVANCE
        now = self._clock.now()
        previous = await uow.identity_results.get(author.id)
        if author.is_official:
            # Las cuentas oficiales no se envían al microservicio de identidad.
            if previous is None:
                await uow.identity_results.save(
                    IdentityResult(
                        author_id=author.id,
                        status=IdentityStatus.NO_IDENTIFICADO,
                        validated_at=now,
                        username=author.username,
                    )
                )
            return ADVANCE
        if self._ttl.is_fresh(previous, username=author.username, now=now):
            return ADVANCE
        try:
            found = await self._resolver.lookup(author)
        except IdentityServiceUnavailableError:
            await uow.identity_results.save(
                IdentityResult(
                    author_id=author.id,
                    status=IdentityStatus.PENDIENTE,
                    validated_at=now,
                    username=author.username,
                )
            )
            await uow.jobs.enqueue(
                Stage.IDENTITY_RETRY, author.id, available_at=now + self._backoff.delay(0)
            )
            return ADVANCE
        await self._resolver.save_definitive(uow, author, found, previous)
        return ADVANCE


class RetryIdentityValidation:
    """Etapa ``identidad_reintento`` (sujeto: autor).

    Si el servicio sigue sin responder, lanza el error para que el runner reprograme
    el trabajo con backoff; en el último intento deja al autor en ``error``.
    """

    def __init__(
        self,
        identity: IdentityService,
        pseudonymizer: Pseudonymizer,
        clock: Clock,
        *,
        max_attempts: int,
    ) -> None:
        self._resolver = _IdentityResolver(identity, pseudonymizer, clock)
        self._clock = clock
        self._max_attempts = max_attempts

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        author = await uow.authors.get(AuthorId(job.subject_id))
        if author is None:
            return STOP
        previous = await uow.identity_results.get(author.id)
        if previous is not None and previous.status.is_definitive:
            return STOP
        try:
            found = await self._resolver.lookup(author)
        except IdentityServiceUnavailableError:
            if job.attempts + 1 < self._max_attempts:
                raise
            await uow.identity_results.save(
                IdentityResult(
                    author_id=author.id,
                    status=IdentityStatus.ERROR,
                    validated_at=self._clock.now(),
                    username=author.username,
                    attempts=job.attempts + 1,
                )
            )
            return STOP
        await self._resolver.save_definitive(uow, author, found, previous)
        return STOP


class RevalidateIdentity:
    """Revalidación manual (p. ej. de un autor en ``error``)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, author_id: AuthorId) -> None:
        async with self._uow_factory() as uow:
            author = await uow.authors.get(author_id)
            if author is None:
                raise NotFoundError(f"el autor {author_id} no existe")
            now = self._clock.now()
            await uow.identity_results.save(
                IdentityResult(
                    author_id=author.id,
                    status=IdentityStatus.PENDIENTE,
                    validated_at=now,
                    username=author.username,
                )
            )
            await uow.jobs.enqueue(Stage.IDENTITY_RETRY, author.id, available_at=now)
            await uow.commit()
