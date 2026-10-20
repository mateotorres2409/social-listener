"""Construye la API con fakes para las pruebas."""

from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager

import httpx

from social_listener.adapters.inbound.http.app import ApiDependencies, create_app
from social_listener.application.use_cases.authors.identity import RevalidateIdentity
from social_listener.application.use_cases.classification.manage import (
    ConfigureModelChain,
    CorrectClassification,
    ManageTaxonomy,
)
from social_listener.application.use_cases.export.assemble import EnrichedMentionAssembler
from social_listener.application.use_cases.ingestion.ingest import (
    ListDeadLetters,
    ReprocessDeadLetter,
)
from social_listener.application.use_cases.ingestion.queries import ManageListeningQueries
from social_listener.application.use_cases.mentions import ViewAuthors, ViewMentions
from social_listener.application.use_cases.privacy.retention import SuppressAuthorData
from social_listener.application.use_cases.projects import ManageProjects
from social_listener.bootstrap.models import ProviderRegistry, ProviderSpec
from tests.fakes.memory import FixedClock, InMemoryDatabase
from tests.fakes.services import HmacPseudonymizer

TOKEN = "token-de-prueba"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def fake_registry() -> ProviderRegistry:
    from social_listener.adapters.outbound.llm.chain import NullClassifier

    return ProviderRegistry(
        {name: ProviderSpec(lambda route: NullClassifier()) for name in ("ollama", "anthropic")}
    )


def fake_dependencies(
    db: InMemoryDatabase,
    clock: FixedClock,
    connectors: Collection[str] = ("x", "mock"),
    registry: ProviderRegistry | None = None,
) -> ApiDependencies:
    pseudonymizer = HmacPseudonymizer()
    return ApiDependencies(
        api_token=TOKEN,
        projects=ManageProjects(db),
        queries=ManageListeningQueries(db, known_connectors=connectors),
        list_dead_letters=ListDeadLetters(db),
        reprocess_dead_letter=ReprocessDeadLetter(db, clock),
        suppress_author=SuppressAuthorData(db, pseudonymizer, clock),
        taxonomy=ManageTaxonomy(db),
        correct_classification=CorrectClassification(
            db, EnrichedMentionAssembler(pseudonymizer), clock
        ),
        mentions=ViewMentions(db),
        model_chain=ConfigureModelChain(db, registry or fake_registry()),
        authors=ViewAuthors(db),
        revalidate_identity=RevalidateIdentity(db, clock),
    )


@asynccontextmanager
async def api_client(deps: ApiDependencies) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(deps))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client
