import re
from pathlib import Path

from social_listener.domain.privacy.model import SensitiveDataType

DOC = Path(__file__).parents[2] / "docs" / "proteccion-datos.md"


def test_marcadores_documentados_coinciden_con_los_implementados() -> None:
    texto = DOC.read_text(encoding="utf-8")
    tabla = texto.split("| Dato | Marcador |", 1)[1].split("\n\n", 1)[0]
    documentados = set(re.findall(r"`(\[[A-Z]+\])`", tabla))

    assert documentados == {t.marker for t in SensitiveDataType}
