"""Utilidades de esquema compartidas por los adaptadores de modelos."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

UNSUPPORTED_KEYWORDS = frozenset({"minimum", "maximum", "minLength", "maxLength", "multipleOf"})


def strip_constraints(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Quita restricciones numéricas y de longitud que algunas APIs no aceptan.

    Esos rangos se validan igual del lado del cliente en ``parse_model_output``.
    """

    def clean(node: Any) -> Any:
        if isinstance(node, Mapping):
            return {k: clean(v) for k, v in node.items() if k not in UNSUPPORTED_KEYWORDS}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    result: dict[str, Any] = clean(schema)
    return result


REPAIR_INSTRUCTION = (
    "Tu respuesta anterior no cumple el esquema solicitado ({error}). "
    "Responde de nuevo solo con el JSON válido según el esquema."
)
