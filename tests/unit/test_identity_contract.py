from pathlib import Path

import yaml
from openapi_spec_validator import validate

CONTRACT = Path(__file__).parents[2] / "contracts" / "identity-service.openapi.yaml"


def load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def test_contrato_openapi_valido() -> None:
    validate(load())


def test_contrato_minimiza_los_datos_de_la_solicitud() -> None:
    request = load()["components"]["schemas"]["LookupRequest"]
    assert set(request["properties"]) == {"channel", "external_author_id", "username"}
    assert request["additionalProperties"] is False
