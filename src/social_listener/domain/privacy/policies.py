"""Políticas puras de protección de datos."""

from __future__ import annotations

from collections.abc import Iterable

from social_listener.domain.privacy.model import Detection, MaskedText


def luhn_valid(number: str) -> bool:
    """Valida un número de tarjeta con el algoritmo de Luhn (ignora espacios y guiones)."""
    digits = [c for c in number if c not in " -"]
    if not 13 <= len(digits) <= 19 or not all(c.isdigit() for c in digits):
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def mask(text: str, detections: Iterable[Detection]) -> MaskedText:
    """Reemplaza cada detección por su marcador tipado.

    Si dos detecciones se solapan, gana la que empieza antes (y, a igual inicio, la
    más larga), para que ningún fragmento quede a medio enmascarar.
    """
    chosen: list[Detection] = []
    for detection in sorted(detections, key=lambda d: (d.start, -(d.end - d.start))):
        if detection.end > len(text):
            continue
        if chosen and detection.start < chosen[-1].end:
            continue
        chosen.append(detection)
    parts: list[str] = []
    cursor = 0
    for detection in chosen:
        parts.append(text[cursor : detection.start])
        parts.append(detection.kind.marker)
        cursor = detection.end
    parts.append(text[cursor:])
    return MaskedText("".join(parts), frozenset(d.kind for d in chosen))
