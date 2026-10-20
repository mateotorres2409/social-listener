"""Conector simulado: genera menciones sintéticas deterministas por tópico."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from social_listener.application.dto.sources import ConnectorBatch, RawMention
from social_listener.domain.mentions.model import ListeningQuery

TEMPLATES = (
    "La app de {marca} no me deja iniciar sesión desde ayer, ¿qué pasa?",
    "Me cobraron dos veces la cuota de manejo de la tarjeta, exijo la devolución",
    "Excelente la atención en la oficina de {marca}, me resolvieron todo rápido",
    "¿Cómo solicito un crédito de libre inversión con {marca}?",
    "Ojo: hay un perfil falso de {marca} pidiendo claves por mensaje directo",
    "Sugiero que {marca} amplíe el horario de atención en cajeros",
    "Mi número de cuenta es 0123456789 y no me llega la transferencia",
    "Gracias {marca} por el aumento de cupo, muy fácil el proceso",
)


class MockConnector:
    """Entrega ``batches`` lotes de ``batch_size`` menciones y luego indica el final."""

    name = "mock"

    def __init__(
        self,
        *,
        batches: int = 3,
        batch_size: int = 5,
        start: datetime | None = None,
    ) -> None:
        self._batches = batches
        self._batch_size = batch_size
        self._start = start or datetime(2026, 1, 1, tzinfo=UTC)

    async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch:
        index = int(checkpoint or 0)
        if index >= self._batches:
            return ConnectorBatch(mentions=[], next_checkpoint=checkpoint, has_more=False)
        rng = random.Random(f"{query.id}:{index}")  # noqa: S311 - datos sintéticos
        brand = (query.terms or query.accounts or query.hashtags or ("la entidad",))[0]
        mentions = [
            self._mention(query, rng, brand, index * self._batch_size + offset)
            for offset in range(self._batch_size)
        ]
        return ConnectorBatch(
            mentions=mentions,
            next_checkpoint=str(index + 1),
            has_more=index + 1 < self._batches,
        )

    def _mention(
        self, query: ListeningQuery, rng: random.Random, brand: str, number: int
    ) -> RawMention:
        author = rng.randint(1, 40)
        external_id = f"{query.id}-{number}"
        return RawMention(
            channel="mock",
            external_id=external_id,
            url=f"https://mock.local/{external_id}",
            text=rng.choice(TEMPLATES).format(marca=brand),
            language="es",
            published_at=self._start + timedelta(minutes=number),
            author_external_id=f"mock-{author}",
            author_username=f"usuario_{author}",
            author_display_name=f"Usuario {author}",
            author_followers=rng.choice((15, 230, 1_800, 25_000)),
            author_verified=False,
            likes=rng.randint(0, 80),
            shares=rng.randint(0, 20),
            replies=rng.randint(0, 10),
        )
