"""Arranque de la API de administración."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from social_listener.adapters.inbound.http.app import ApiDependencies, create_app
from social_listener.bootstrap.container import Container, build_container


def build_api(container: Container) -> FastAPI:
    deps = ApiDependencies(
        api_token=container.settings.api_token.get_secret_value(),
        projects=container.manage_projects(),
        queries=container.manage_queries(),
        list_dead_letters=container.list_dead_letters(),
        reprocess_dead_letter=container.reprocess_dead_letter(),
        suppress_author=container.suppress_author(),
        taxonomy=container.manage_taxonomy(),
        correct_classification=container.correct_classification(),
        mentions=container.view_mentions(),
        model_chain=container.configure_model_chain(),
        authors=container.view_authors(),
        revalidate_identity=container.revalidate_identity(),
    )
    app = create_app(deps)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await container.aclose()

    app.router.lifespan_context = lifespan
    return app


def app_factory() -> FastAPI:
    """Punto de entrada para ``uvicorn --factory``."""
    return build_api(build_container())
