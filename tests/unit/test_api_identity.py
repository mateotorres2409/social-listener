from datetime import timedelta

from social_listener.application.dto.pipeline import Stage
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.mentions.model import Channel
from tests.contracts.persistence import P1, T0
from tests.fakes.memory import FixedClock
from tests.unit.api_support import AUTH, api_client, fake_dependencies
from tests.unit.test_individualize import process, seeded


async def test_revalidacion_manual_por_api() -> None:
    clock = FixedClock(T0 + timedelta(days=1))
    db = await seeded(clock)
    await process(db, clock, "m-1")
    async with db() as uow:
        author = await uow.authors.get_by_external(P1, Channel.X, "a-1")
    assert author is not None

    async with api_client(fake_dependencies(db, clock)) as api:
        response = await api.post(f"/v1/authors/{author.id}/identity/revalidate", headers=AUTH)
        missing = await api.post("/v1/authors/no-existe/identity/revalidate", headers=AUTH)

    assert response.status_code == 202
    assert missing.status_code == 404
    async with db() as uow:
        result = await uow.identity_results.get(author.id)
        assert result is not None
        assert result.status is IdentityStatus.PENDIENTE
        assert [j.subject_id for j in uow.jobs.pending(Stage.IDENTITY_RETRY)] == [author.id]
