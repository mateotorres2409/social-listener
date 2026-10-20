import json
import re
from pathlib import Path

import jsonschema

from tests.unit.test_identity_contract import load

DOC = Path(__file__).parents[2] / "docs" / "identidad.md"


def test_ejemplos_de_la_documentacion_cumplen_el_openapi() -> None:
    schemas = load()["components"]["schemas"]
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", DOC.read_text("utf-8"), re.S)]
    request, *responses = blocks

    jsonschema.validate(request, schemas["LookupRequest"])
    assert {r["status"] for r in responses} == {"match", "no_match", "ambiguous"}
    for response in responses:
        jsonschema.validate(response, schemas["LookupResponse"])

    examples = load()["paths"]["/v1/identity/lookups"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["examples"]
    assert sorted(e["value"]["status"] for e in examples.values()) == sorted(
        r["status"] for r in responses
    )
