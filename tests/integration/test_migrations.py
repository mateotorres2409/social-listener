import asyncio
from typing import Any

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.integration

TABLAS = {
    "projects",
    "model_chains",
    "listening_queries",
    "connector_checkpoints",
    "mentions",
    "mention_queries",
    "authors",
    "author_username_history",
    "threads",
    "classifications",
    "taxonomies",
    "topics",
    "identity_results",
    "pipeline_jobs",
    "dead_letters",
    "export_outbox",
    "audit_log",
}


def _inspeccionar(url: str, fn: Any) -> Any:
    async def run() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(lambda sync: fn(inspect(sync)))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _tablas(url: str) -> set[str]:
    return set(_inspeccionar(url, lambda i: i.get_table_names())) - {"alembic_version"}


def test_upgrade_y_downgrade(postgres_url: str) -> None:
    config = alembic_config(postgres_url)

    command.upgrade(config, "head")
    assert _tablas(postgres_url) == TABLAS

    command.downgrade(config, "base")
    assert _tablas(postgres_url) == set()

    command.upgrade(config, "head")
    assert _tablas(postgres_url) == TABLAS


def test_mentions_separa_texto_original_y_enmascarado(postgres_url: str) -> None:
    command.upgrade(alembic_config(postgres_url), "head")
    columnas = {c["name"] for c in _inspeccionar(postgres_url, lambda i: i.get_columns("mentions"))}
    unicas = [
        u["column_names"]
        for u in _inspeccionar(postgres_url, lambda i: i.get_unique_constraints("mentions"))
    ]
    assert {"raw_text", "masked_text"} <= columnas
    assert ["channel", "external_id"] in unicas
