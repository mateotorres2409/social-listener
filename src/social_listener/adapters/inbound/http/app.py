"""API de administración (FastAPI)."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from social_listener import __version__
from social_listener.application.errors import NotFoundError
from social_listener.application.use_cases.authors.identity import RevalidateIdentity
from social_listener.application.use_cases.classification.manage import (
    ConfigureModelChain,
    CorrectClassification,
    ManageTaxonomy,
)
from social_listener.application.use_cases.ingestion.ingest import (
    ListDeadLetters,
    ReprocessDeadLetter,
)
from social_listener.application.use_cases.ingestion.queries import ManageListeningQueries
from social_listener.application.use_cases.mentions import ViewAuthors, ViewMentions
from social_listener.application.use_cases.privacy.retention import SuppressAuthorData
from social_listener.application.use_cases.projects import ManageProjects
from social_listener.domain.shared.errors import DomainError


@dataclass
class ApiDependencies:
    api_token: str
    projects: ManageProjects
    queries: ManageListeningQueries
    list_dead_letters: ListDeadLetters
    reprocess_dead_letter: ReprocessDeadLetter
    suppress_author: SuppressAuthorData
    taxonomy: ManageTaxonomy
    correct_classification: CorrectClassification
    mentions: ViewMentions
    model_chain: ConfigureModelChain
    authors: ViewAuthors
    revalidate_identity: RevalidateIdentity
    extra: dict[str, Any] = field(default_factory=dict)


def get_deps(request: Request) -> ApiDependencies:
    deps: ApiDependencies = request.app.state.deps
    return deps


Deps = Annotated[ApiDependencies, Depends(get_deps)]

_bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected: str = request.app.state.deps.api_token
    provided = credentials.credentials if credentials else ""
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app(
    deps: ApiDependencies, *, extra_routers: list[Callable[[FastAPI], None]] | None = None
) -> FastAPI:
    from social_listener.adapters.inbound.http.routers import (
        authors,
        classification,
        dead_letters,
        privacy,
        projects,
        queries,
    )

    app = FastAPI(title="Social Listener", version=__version__)
    app.state.deps = deps

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def invalid(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    protected = [Depends(require_token)]
    app.include_router(projects.router, prefix="/v1", dependencies=protected)
    app.include_router(queries.router, prefix="/v1", dependencies=protected)
    app.include_router(dead_letters.router, prefix="/v1", dependencies=protected)
    app.include_router(privacy.router, prefix="/v1", dependencies=protected)
    app.include_router(classification.router, prefix="/v1", dependencies=protected)
    app.include_router(authors.router, prefix="/v1", dependencies=protected)
    for register in extra_routers or []:
        register(app)
    return app
