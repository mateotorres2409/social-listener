from pathlib import Path

import pytest

from social_listener.adapters.outbound.connectors.fixture import FixtureConnector
from social_listener.adapters.outbound.connectors.mock import MockConnector
from social_listener.application.errors import SourceError
from social_listener.application.use_cases.ingestion.normalize import InvalidMention, normalize
from tests.contracts.services import QUERY, SourceConnectorContract

FIXTURE = Path(__file__).parents[1] / "fixtures" / "menciones_basicas.jsonl"


class TestMockConnector(SourceConnectorContract):
    @pytest.fixture
    def connector(self) -> MockConnector:
        return MockConnector(batches=3, batch_size=4)


class TestFixtureConnector(SourceConnectorContract):
    @pytest.fixture
    def connector(self) -> FixtureConnector:
        return FixtureConnector(FIXTURE, batch_size=2)


async def test_mock_genera_menciones_validas_y_deterministas() -> None:
    connector = MockConnector(batches=1, batch_size=10)
    first = await connector.fetch(QUERY, None)
    again = await connector.fetch(QUERY, None)

    assert first == again
    for raw in first.mentions:
        assert not isinstance(normalize(raw), InvalidMention)
        assert raw.channel == "mock"


async def test_fixture_entrega_exactamente_las_menciones_del_archivo_en_orden() -> None:
    connector = FixtureConnector(FIXTURE, batch_size=2)
    entregadas = []
    checkpoint = None
    while True:
        batch = await connector.fetch(QUERY, checkpoint)
        entregadas.extend(batch.mentions)
        checkpoint = batch.next_checkpoint
        if not batch.has_more:
            break

    assert [m.external_id for m in entregadas] == ["f-1", "f-2", "f-3"]
    assert [m.channel for m in entregadas] == ["x", "instagram", "x"]
    assert entregadas[1].author_followers == 300
    assert entregadas[2].parent_external_id == "f-1"
    assert entregadas[2].source_payload == {"campo_propio_de_la_fuente": 7}


async def test_fixture_inexistente_es_error_definitivo(tmp_path: Path) -> None:
    with pytest.raises(SourceError):
        await FixtureConnector(tmp_path / "no-existe.jsonl").fetch(QUERY, None)
