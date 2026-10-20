"""Orden del pipeline: qué etapa sigue a cada una."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from social_listener.application.dto.pipeline import Stage

TRANSITIONS: Mapping[Stage, Stage | None] = MappingProxyType(
    {
        Stage.PROTECT: Stage.CLASSIFY,
        Stage.CLASSIFY: Stage.INDIVIDUALIZE,
        Stage.INDIVIDUALIZE: Stage.IDENTITY,
        Stage.IDENTITY: Stage.EXPORT,
        Stage.EXPORT: None,
        Stage.IDENTITY_RETRY: None,
    }
)
"""La normalización ocurre dentro de la ingesta, que encola ``PROTECT``."""

FIRST_STAGE = Stage.PROTECT


def next_stage(stage: Stage) -> Stage | None:
    return TRANSITIONS[stage]
