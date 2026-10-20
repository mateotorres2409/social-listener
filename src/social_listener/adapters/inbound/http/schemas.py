"""Esquemas HTTP (Pydantic) y su mapeo a objetos de aplicación y dominio."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from social_listener.application.dto.pipeline import DeadLetter
from social_listener.application.use_cases.ingestion.queries import ListeningQueryInput
from social_listener.domain.classification.impact import ImpactThresholds
from social_listener.domain.mentions.model import Channel, ListeningQuery
from social_listener.domain.projects.model import OfficialAccount, Project, RecurrenceRule
from social_listener.domain.shared.types import ProjectId


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OfficialAccountSchema(ApiModel):
    channel: Channel
    external_id: str = Field(min_length=1)


class ImpactSchema(ApiModel):
    influencer_followers: int = 10_000
    medium_followers: int = 1_000
    viral_interactions: int = 500
    medium_interactions: int = 50


class RecurrenceSchema(ApiModel):
    threshold: int = 3
    window_days: int = 30


class ProjectIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    retention_days: int = 365
    review_confidence_threshold: float = 0.6
    impact: ImpactSchema = ImpactSchema()
    recurrence: RecurrenceSchema = RecurrenceSchema()
    official_accounts: list[OfficialAccountSchema] = []

    def to_domain(self, project_id: str) -> Project:
        return Project(
            id=ProjectId(project_id),
            name=self.name,
            retention_days=self.retention_days,
            review_confidence_threshold=self.review_confidence_threshold,
            impact=ImpactThresholds(**self.impact.model_dump()),
            recurrence=RecurrenceRule(**self.recurrence.model_dump()),
            official_accounts=frozenset(
                OfficialAccount(a.channel, a.external_id) for a in self.official_accounts
            ),
        )


class ProjectOut(ProjectIn):
    id: str

    @classmethod
    def from_domain(cls, project: Project) -> ProjectOut:
        return cls(
            id=project.id,
            name=project.name,
            retention_days=project.retention_days,
            review_confidence_threshold=project.review_confidence_threshold,
            impact=ImpactSchema(
                influencer_followers=project.impact.influencer_followers,
                medium_followers=project.impact.medium_followers,
                viral_interactions=project.impact.viral_interactions,
                medium_interactions=project.impact.medium_interactions,
            ),
            recurrence=RecurrenceSchema(
                threshold=project.recurrence.threshold,
                window_days=project.recurrence.window_days,
            ),
            official_accounts=[
                OfficialAccountSchema(channel=a.channel, external_id=a.external_id)
                for a in sorted(
                    project.official_accounts, key=lambda a: (a.channel.value, a.external_id)
                )
            ],
        )


class ListeningQueryIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    channels: list[str] = Field(min_length=1)
    terms: list[str] = []
    accounts: list[str] = []
    hashtags: list[str] = []
    languages: list[str] = []

    def to_input(self) -> ListeningQueryInput:
        return ListeningQueryInput(
            name=self.name,
            channels=tuple(self.channels),
            terms=tuple(self.terms),
            accounts=tuple(self.accounts),
            hashtags=tuple(self.hashtags),
            languages=tuple(self.languages),
        )


class ListeningQueryOut(ListeningQueryIn):
    id: str
    project_id: str
    status: str

    @classmethod
    def from_domain(cls, query: ListeningQuery) -> ListeningQueryOut:
        return cls(
            id=query.id,
            project_id=query.project_id,
            status=query.status.value,
            name=query.name,
            channels=list(query.channels),
            terms=list(query.terms),
            accounts=list(query.accounts),
            hashtags=list(query.hashtags),
            languages=list(query.languages),
        )


class DeadLetterOut(ApiModel):
    id: str
    stage: str
    reason: str
    attempts: int
    created_at: datetime
    subject_id: str | None

    @classmethod
    def from_domain(cls, letter: DeadLetter) -> DeadLetterOut:
        return cls(
            id=letter.id,
            stage=letter.stage.value,
            reason=letter.reason,
            attempts=letter.attempts,
            created_at=letter.created_at,
            subject_id=letter.subject_id,
        )


class ReprocessOut(ApiModel):
    stage: str
    accepted: bool
    reason: str | None = None


class ErrorOut(ApiModel):
    detail: Any
