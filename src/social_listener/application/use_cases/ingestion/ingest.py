"""Ingesta incremental: conectores → normalización → deduplicación → pipeline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from social_listener.application.backoff import Backoff
from social_listener.application.dto.pipeline import DeadLetter, Stage
from social_listener.application.dto.sources import ConnectorBatch, RawMention
from social_listener.application.errors import (
    NotFoundError,
    RateLimitedError,
    SourceError,
    TransientSourceError,
)
from social_listener.application.pipeline import FIRST_STAGE
from social_listener.application.ports.outbound.services import Clock, SourceConnector
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork, UnitOfWorkFactory
from social_listener.application.use_cases.ingestion.normalize import (
    InvalidMention,
    normalize,
    raw_from_payload,
    raw_to_payload,
)
from social_listener.domain.mentions.model import ListeningQuery
from social_listener.domain.shared.types import ListeningQueryId, ProjectId, new_id

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class IngestionReport:
    created: int = 0
    duplicates: int = 0
    invalid: int = 0
    batches: int = 0
    failures: list[str] = field(default_factory=list)


class IngestMentions:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        connectors: Mapping[str, SourceConnector],
        clock: Clock,
        *,
        backoff: Backoff | None = None,
        max_retries: int = 5,
        max_batches_per_cycle: int = 20,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._uow_factory = uow_factory
        self._connectors = connectors
        self._clock = clock
        self._backoff = backoff or Backoff(base_s=1.0, max_s=120.0)
        self._max_retries = max_retries
        self._max_batches = max_batches_per_cycle
        self._sleep = sleep

    async def run_cycle(self) -> IngestionReport:
        """Ejecuta un ciclo de captura para todos los tópicos activos."""
        report = IngestionReport()
        async with self._uow_factory() as uow:
            queries = list(await uow.queries.list_active())
        for query in queries:
            for channel in query.channels:
                connector = self._connectors.get(channel)
                if connector is None:
                    report.failures.append(f"{query.id}:{channel}: sin conector")
                    continue
                await self._ingest_query(query, connector, report)
        return report

    async def _ingest_query(
        self, query: ListeningQuery, connector: SourceConnector, report: IngestionReport
    ) -> None:
        async with self._uow_factory() as uow:
            checkpoint = await uow.checkpoints.get(query.id, connector.name)
        for _ in range(self._max_batches):
            try:
                batch = await self._fetch_with_retry(connector, query, checkpoint)
            except (SourceError, TransientSourceError) as exc:
                # El checkpoint no avanza: el próximo ciclo reintenta desde el mismo punto.
                report.failures.append(f"{query.id}:{connector.name}: {type(exc).__name__}")
                logger.warning(
                    "falló la captura del tópico %s en %s: %s",
                    query.id,
                    connector.name,
                    type(exc).__name__,
                )
                return
            await self._store_batch(query, connector.name, batch, report)
            report.batches += 1
            checkpoint = batch.next_checkpoint
            if not batch.has_more:
                return

    async def _fetch_with_retry(
        self, connector: SourceConnector, query: ListeningQuery, checkpoint: str | None
    ) -> ConnectorBatch:
        attempt = 0
        while True:
            try:
                return await connector.fetch(query, checkpoint)
            except TransientSourceError as exc:
                if attempt >= self._max_retries:
                    raise
                delay = self._backoff.delay(attempt).total_seconds()
                if isinstance(exc, RateLimitedError) and exc.retry_after_s is not None:
                    delay = max(delay, exc.retry_after_s)
                attempt += 1
                await self._sleep(delay)

    async def _store_batch(
        self,
        query: ListeningQuery,
        connector_name: str,
        batch: ConnectorBatch,
        report: IngestionReport,
    ) -> None:
        async with self._uow_factory() as uow:
            for raw in batch.mentions:
                await self._store_one(uow, query.project_id, query.id, raw, connector_name, report)
            if batch.next_checkpoint is not None:
                await uow.checkpoints.save(query.id, connector_name, batch.next_checkpoint)
            await uow.commit()

    async def _store_one(
        self,
        uow: UnitOfWork,
        project_id: ProjectId,
        query_id: ListeningQueryId,
        raw: RawMention,
        default_channel: str,
        report: IngestionReport,
    ) -> None:
        result = normalize(raw, default_channel)
        if isinstance(result, InvalidMention):
            report.invalid += 1
            await uow.dead_letters.add(
                DeadLetter(
                    id=new_id(),
                    stage=Stage.NORMALIZE,
                    reason="; ".join(result.reasons),
                    attempts=1,
                    created_at=self._clock.now(),
                    payload={
                        "project_id": project_id,
                        "query_id": query_id,
                        "default_channel": default_channel,
                        "raw": raw_to_payload(raw),
                    },
                )
            )
            return
        mention, created = await uow.mentions.add_or_get(project_id, result, query_id)
        if created:
            report.created += 1
            await uow.jobs.enqueue(FIRST_STAGE, mention.id)
        else:
            report.duplicates += 1


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    stage: Stage
    accepted: bool
    reason: str | None = None


class ListDeadLetters:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, limit: int = 100) -> Sequence[DeadLetter]:
        async with self._uow_factory() as uow:
            return await uow.dead_letters.list_recent(limit)


class ReprocessDeadLetter:
    """Reingresa al pipeline una mención de la cola de errores desde la etapa en que falló."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, letter_id: str) -> ReprocessResult:
        async with self._uow_factory() as uow:
            letter = await uow.dead_letters.get(letter_id)
            if letter is None:
                raise NotFoundError(f"elemento {letter_id} no existe en la cola de errores")
            if letter.stage is Stage.NORMALIZE:
                payload = dict(letter.payload)
                raw = raw_from_payload(dict(payload["raw"]))  # type: ignore[call-overload]
                result = normalize(raw, str(payload.get("default_channel") or "") or None)
                if isinstance(result, InvalidMention):
                    return ReprocessResult(letter.stage, False, "; ".join(result.reasons))
                mention, created = await uow.mentions.add_or_get(
                    ProjectId(str(payload["project_id"])),
                    result,
                    ListeningQueryId(str(payload["query_id"])),
                )
                if created:
                    await uow.jobs.enqueue(FIRST_STAGE, mention.id)
            else:
                if letter.subject_id is None:
                    return ReprocessResult(letter.stage, False, "sin sujeto")
                await uow.jobs.enqueue(letter.stage, letter.subject_id)
            await uow.dead_letters.remove(letter.id)
            await uow.commit()
        return ReprocessResult(letter.stage, True)
