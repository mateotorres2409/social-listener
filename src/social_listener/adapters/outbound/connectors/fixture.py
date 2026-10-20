"""Conector de reproducción de fixtures: entrega menciones grabadas en JSON Lines.

Cada línea es un objeto con los campos de ``RawMention``; ``published_at`` va en
ISO 8601 con zona horaria. El checkpoint es el número de líneas ya entregadas.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from social_listener.application.dto.sources import ConnectorBatch, RawMention
from social_listener.application.errors import SourceError
from social_listener.domain.mentions.model import ListeningQuery

FIELDS = frozenset(RawMention.__dataclass_fields__) - {"source_payload"}


class FixtureConnector:
    name = "fixture"

    def __init__(self, path: Path, *, batch_size: int = 50) -> None:
        self._path = path
        self._batch_size = batch_size

    def _load(self) -> list[RawMention]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SourceError(f"no se pudo leer el fixture {self._path.name}") from exc
        return [_parse(line) for line in lines if line.strip()]

    async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch:
        mentions = self._load()
        start = int(checkpoint or 0)
        end = min(start + self._batch_size, len(mentions))
        if start >= len(mentions):
            return ConnectorBatch(mentions=[], next_checkpoint=checkpoint, has_more=False)
        return ConnectorBatch(
            mentions=mentions[start:end],
            next_checkpoint=str(end),
            has_more=end < len(mentions),
        )


def _parse(line: str) -> RawMention:
    data: dict[str, Any] = json.loads(line)
    values = {k: v for k, v in data.items() if k in FIELDS}
    if isinstance(values.get("published_at"), str):
        values["published_at"] = datetime.fromisoformat(values["published_at"])
    extra = {k: v for k, v in data.items() if k not in FIELDS}
    return RawMention(**values, source_payload=extra)
