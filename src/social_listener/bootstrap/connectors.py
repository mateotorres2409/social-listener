"""Registro de conectores de fuentes disponibles según la configuración."""

from __future__ import annotations

from social_listener.adapters.outbound.connectors.fixture import FixtureConnector
from social_listener.adapters.outbound.connectors.mock import MockConnector
from social_listener.application.ports.outbound.services import SourceConnector
from social_listener.bootstrap.settings import Settings


def build_connectors(settings: Settings) -> dict[str, SourceConnector]:
    connectors: dict[str, SourceConnector] = {}
    if settings.enable_mock_connector:
        connectors["mock"] = MockConnector()
    if settings.fixture_path is not None:
        connectors["fixture"] = FixtureConnector(settings.fixture_path)
    return connectors
