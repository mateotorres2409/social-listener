import re
from pathlib import Path

from social_listener.adapters.outbound.otel import schema as s

DOC = Path(__file__).parents[2] / "docs" / "esquema-otel.md"


def _section(title: str) -> str:
    text = DOC.read_text("utf-8")
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def test_tabla_de_atributos_coincide_con_schema_py() -> None:
    documented = set(
        re.findall(r"^\| `(social\.[a-z_.]+)` \|", _section("Atributos de una mención"), re.M)
    )
    assert documented == set(s.ALL_MENTION_ATTRIBUTES)


def test_eventos_y_severidades_documentados() -> None:
    events = set(re.findall(r"^\| `(social\.[a-z_.]+)` \|", _section("Eventos"), re.M))
    assert events == {s.MENTION_ENRICHED, s.MENTION_UPDATED, s.AUTHOR_IDENTITY_RESOLVED}
    severities = dict(re.findall(r"^\| `(\w+)` \| (\w+) \|", _section("Severidad"), re.M))
    assert severities == {impact: text for impact, (_, text) in s.SEVERITY_BY_IMPACT.items()}
    assert s.SCHEMA_VERSION in DOC.read_text("utf-8")
