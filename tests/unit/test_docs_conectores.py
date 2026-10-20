import re
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _normalizar(texto: str) -> list[str]:
    return [linea.strip() for linea in texto.splitlines() if linea.strip()]


def test_ejemplo_de_la_documentacion_corresponde_al_conector_mock() -> None:
    doc = (ROOT / "docs" / "conectores.md").read_text(encoding="utf-8")
    fuente = _normalizar(
        (ROOT / "src/social_listener/adapters/outbound/connectors/mock.py").read_text("utf-8")
    )
    bloques = re.findall(r"```python\n(.*?)```", doc, flags=re.S)
    ejemplo = next(b for b in bloques if "class MockConnector" in b)

    for linea in _normalizar(ejemplo):
        assert linea in fuente, f"la línea del ejemplo no está en mock.py: {linea}"
