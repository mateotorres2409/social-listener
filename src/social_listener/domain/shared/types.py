"""Identificadores y utilidades de tipos compartidas por el dominio."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import NewType

from social_listener.domain.shared.errors import require

ProjectId = NewType("ProjectId", str)
ListeningQueryId = NewType("ListeningQueryId", str)
MentionId = NewType("MentionId", str)
AuthorId = NewType("AuthorId", str)
ThreadId = NewType("ThreadId", str)


def new_id() -> str:
    return str(uuid.uuid4())


def require_aware(moment: datetime, field: str) -> None:
    require(moment.tzinfo is not None, f"{field} debe tener zona horaria")


def require_unit_interval(value: float, field: str) -> None:
    require(0.0 <= value <= 1.0, f"{field} debe estar entre 0 y 1")
