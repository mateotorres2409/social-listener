from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import Field

from social_listener.adapters.inbound.http.app import Deps
from social_listener.adapters.inbound.http.schemas import ApiModel
from social_listener.application.dto.classification import ModelRoute
from social_listener.application.use_cases.mentions import MentionView
from social_listener.domain.classification.model import (
    Category,
    Dimension,
    MentionClassification,
    Subcategory,
    Taxonomy,
)
from social_listener.domain.shared.types import MentionId, ProjectId

router = APIRouter(tags=["clasificación"])


class SubcategorySchema(ApiModel):
    code: str
    name: str


class CategorySchema(ApiModel):
    code: str
    name: str
    subcategories: list[SubcategorySchema] = Field(default_factory=list)


class TaxonomyIn(ApiModel):
    categories: list[CategorySchema]


class TaxonomyOut(TaxonomyIn):
    version: int
    project_id: str | None

    @classmethod
    def from_domain(cls, taxonomy: Taxonomy) -> TaxonomyOut:
        return cls(
            version=taxonomy.version,
            project_id=taxonomy.project_id,
            categories=[
                CategorySchema(
                    code=c.code,
                    name=c.name,
                    subcategories=[
                        SubcategorySchema(code=s.code, name=s.name) for s in c.subcategories
                    ],
                )
                for c in taxonomy.categories
            ],
        )


class DimensionOut(ApiModel):
    value: str
    source: str
    confidence: float
    provider: str | None
    model: str | None


class ClassificationOut(ApiModel):
    dimensions: dict[str, DimensionOut]
    sentiment_score: float
    subcategory: str | None
    taxonomy_version: int
    channel_format: str
    needs_review: bool

    @classmethod
    def from_domain(cls, c: MentionClassification) -> ClassificationOut:
        return cls(
            dimensions={
                d.value: DimensionOut(
                    value=v.value,
                    source=v.source.value,
                    confidence=v.confidence,
                    provider=v.provider,
                    model=v.model,
                )
                for d, v in c.values.items()
            },
            sentiment_score=c.sentiment_score,
            subcategory=c.subcategory,
            taxonomy_version=c.taxonomy_version,
            channel_format=c.channel_format,
            needs_review=c.needs_review,
        )


class MentionOut(ApiModel):
    id: str
    project_id: str
    channel: str
    url: str
    published_at: datetime
    masked_text: str | None
    sensitive_data_types: list[str]
    classification: ClassificationOut | None

    @classmethod
    def from_view(cls, view: MentionView) -> MentionOut:
        m = view.mention
        return cls(
            id=m.id,
            project_id=m.project_id,
            channel=m.data.channel.value,
            url=m.data.url,
            published_at=m.data.published_at,
            masked_text=m.text.value if m.text else None,
            sensitive_data_types=sorted(t.value for t in m.text.detected) if m.text else [],
            classification=ClassificationOut.from_domain(view.classification)
            if view.classification
            else None,
        )


class CorrectionIn(ApiModel):
    corrections: dict[Dimension, str]
    sentiment_score: float | None = None
    subcategory: str | None = None
    actor: str = "analista"


@router.get("/projects/{project_id}/taxonomy")
async def get_taxonomy(project_id: str, deps: Deps) -> TaxonomyOut:
    return TaxonomyOut.from_domain(await deps.taxonomy.active(ProjectId(project_id)))


@router.put("/projects/{project_id}/taxonomy")
async def publish_taxonomy(project_id: str, body: TaxonomyIn, deps: Deps) -> TaxonomyOut:
    categories = tuple(
        Category(c.code, c.name, tuple(Subcategory(s.code, s.name) for s in c.subcategories))
        for c in body.categories
    )
    return TaxonomyOut.from_domain(await deps.taxonomy.publish(ProjectId(project_id), categories))


@router.get("/mentions/{mention_id}")
async def get_mention(mention_id: str, deps: Deps) -> MentionOut:
    return MentionOut.from_view(await deps.mentions.get(MentionId(mention_id)))


@router.post("/mentions/{mention_id}/classification/corrections")
async def correct_classification(
    mention_id: str, body: CorrectionIn, deps: Deps
) -> ClassificationOut:
    updated = await deps.correct_classification.execute(
        MentionId(mention_id),
        body.corrections,
        actor=body.actor,
        sentiment_score=body.sentiment_score,
        subcategory=body.subcategory,
    )
    return ClassificationOut.from_domain(updated)


class ModelRouteSchema(ApiModel):
    provider: str
    model: str
    timeout_s: float = 20.0
    base_url: str | None = None
    credential_ref: str | None = Field(
        default=None, description="Referencia al gestor de secretos; nunca la clave"
    )
    capabilities: list[str] = Field(default_factory=list)
    options: dict[str, str] = Field(default_factory=dict)

    def to_route(self) -> ModelRoute:
        return ModelRoute(
            provider=self.provider,
            model=self.model,
            timeout_s=self.timeout_s,
            base_url=self.base_url,
            credential_ref=self.credential_ref,
            capabilities=frozenset(self.capabilities),
            options=dict(self.options),
        )

    @classmethod
    def from_route(cls, route: ModelRoute) -> ModelRouteSchema:
        return cls(
            provider=route.provider,
            model=route.model,
            timeout_s=route.timeout_s,
            base_url=route.base_url,
            credential_ref=route.credential_ref,
            capabilities=sorted(route.capabilities),
            options=dict(route.options),
        )


class ModelChainIn(ApiModel):
    chain: list[ModelRouteSchema]


@router.get("/projects/{project_id}/model-chain")
async def get_model_chain(project_id: str, deps: Deps) -> ModelChainIn:
    chain = await deps.model_chain.get(ProjectId(project_id))
    return ModelChainIn(chain=[ModelRouteSchema.from_route(r) for r in chain])


@router.put("/projects/{project_id}/model-chain")
async def configure_model_chain(project_id: str, body: ModelChainIn, deps: Deps) -> ModelChainIn:
    chain = await deps.model_chain.configure(
        ProjectId(project_id), [r.to_route() for r in body.chain]
    )
    return ModelChainIn(chain=[ModelRouteSchema.from_route(r) for r in chain])
