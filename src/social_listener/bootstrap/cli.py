"""Línea de comandos: ``social-listener api | worker --stage <etapa> | ingest``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Callable

from social_listener.adapters.inbound.worker.runner import StageRunner
from social_listener.application.dto.pipeline import Stage
from social_listener.application.ports.inbound.stages import StageHandler
from social_listener.bootstrap.container import Container, build_container

logger = logging.getLogger("social_listener")

HandlerFactory = Callable[[Container], StageHandler]
STAGE_HANDLERS: dict[Stage, HandlerFactory] = {
    Stage.PROTECT: Container.protect_mention,
    Stage.CLASSIFY: Container.classify_mention,
    Stage.INDIVIDUALIZE: Container.individualize_author,
    Stage.IDENTITY: Container.validate_identity,
    Stage.IDENTITY_RETRY: Container.retry_identity,
    Stage.EXPORT: Container.export_mention,
}
"""Etapas con caso de uso implementado; cada grupo de capacidad registra la suya."""
RETENTION = "retention"


def _stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    return stop


async def run_worker(stage: Stage) -> None:
    factory = STAGE_HANDLERS.get(stage)
    if factory is None:
        raise SystemExit(f"la etapa {stage.value} no tiene caso de uso registrado")
    container = build_container()
    runner = StageRunner(
        stage,
        factory(container),
        container.uow_factory,
        container.clock,
        instrumentation=container.instrumentation,
    )
    try:
        await runner.run_forever(_stop_event())
    finally:
        await container.aclose()


async def run_retention() -> None:
    container = build_container()
    retention = container.apply_retention()
    stop = _stop_event()
    try:
        while not stop.is_set():
            report = await retention.execute()
            for project in report.projects:
                logger.info(
                    "retención del proyecto %s: %d menciones anonimizadas, %d autores eliminados",
                    project.project_id,
                    project.mentions_purged,
                    project.authors_deleted,
                )
            try:
                await asyncio.wait_for(stop.wait(), container.settings.retention_interval_s)
            except TimeoutError:
                continue
    finally:
        await container.aclose()


async def run_dispatcher() -> None:
    from social_listener.adapters.outbound.otel.dispatcher import OutboxDispatcher
    from social_listener.adapters.outbound.otel.telemetry import otlp_log_exporter

    container = build_container()
    dispatcher = OutboxDispatcher(
        container.engine,
        otlp_log_exporter(),
        container.telemetry.resource,
        container.clock,
    )
    try:
        await dispatcher.run_forever(_stop_event())
    finally:
        dispatcher.shutdown()
        await container.aclose()


async def run_ingest(once: bool) -> None:
    container = build_container()
    ingest = container.ingest_mentions()
    stop = _stop_event()
    try:
        while not stop.is_set():
            report = await ingest.run_cycle()
            logger.info(
                "ciclo de ingesta: %d nuevas, %d duplicadas, %d inválidas, %d fallas",
                report.created,
                report.duplicates,
                report.invalid,
                len(report.failures),
            )
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), container.settings.ingest_interval_s)
            except TimeoutError:
                continue
    finally:
        await container.aclose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="social-listener")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="API de administración")
    api.add_argument("--host", default="0.0.0.0")  # noqa: S104 - se expone dentro del contenedor
    api.add_argument("--port", type=int, default=8000)
    worker = sub.add_parser("worker", help="worker de una etapa del pipeline")
    worker.add_argument("--stage", required=True, choices=[*(s.value for s in Stage), RETENTION])
    sub.add_parser("dispatch", help="despachador del outbox hacia el Collector (OTLP)")
    ingest = sub.add_parser("ingest", help="ciclos de ingesta")
    ingest.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "api":
        import uvicorn

        uvicorn.run(
            "social_listener.bootstrap.api:app_factory",
            factory=True,
            host=args.host,
            port=args.port,
        )
    elif args.command == "dispatch":
        asyncio.run(run_dispatcher())
    elif args.command == "worker" and args.stage == RETENTION:
        asyncio.run(run_retention())
    elif args.command == "worker":
        asyncio.run(run_worker(Stage(args.stage)))
    else:
        asyncio.run(run_ingest(args.once))


if __name__ == "__main__":
    main()
