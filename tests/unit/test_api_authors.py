from __future__ import annotations

from datetime import timedelta

from social_listener.domain.mentions.model import Channel
from tests.contracts.persistence import P1, T0
from tests.fakes.memory import FixedClock
from tests.unit.api_support import AUTH, api_client, fake_dependencies
from tests.unit.test_individualize import process, seeded


async def test_consulta_del_historial_del_autor() -> None:
    clock = FixedClock(T0 + timedelta(days=1))
    db = await seeded(clock)
    await process(db, clock, "m-2", tipo="felicitacion", days=5)
    await process(db, clock, "m-1", tipo="queja", days=1)
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
    assert author is not None

    async with api_client(fake_dependencies(db, clock)) as api:
        response = await api.get(f"/v1/authors/{author.id}", headers=AUTH)
        missing = await api.get("/v1/authors/no-existe", headers=AUTH)

    body = response.json()
    assert response.status_code == 200
    assert body["total_mentions"] == 2
    assert [m["url"].rsplit("/", 1)[1] for m in body["mentions"]] == ["m-1", "m-2"]
    assert body["mentions"][0]["classification"]["dimensions"]["tipo"]["value"] == "queja"
    assert missing.status_code == 404
