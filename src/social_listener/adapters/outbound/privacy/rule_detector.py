"""Detector de datos sensibles por reglas (formatos colombianos).

Orden de prioridad cuando dos reglas cubren el mismo fragmento: correo, tarjeta
(con Luhn), teléfono, documento, cuenta y, por último, cualquier secuencia larga de
dígitos que quede sin clasificar, que se trata como cuenta para no dejarla expuesta.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from social_listener.domain.privacy.model import Detection, SensitiveDataType
from social_listener.domain.privacy.policies import luhn_valid


@dataclass(frozen=True, slots=True)
class Rule:
    kind: SensitiveDataType
    pattern: re.Pattern[str]
    group: int | str = 0
    accept: Callable[[str], bool] | None = None


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
PHONE = re.compile(r"(?<![\d+])(?:\+?57[ -]?)?(?:3\d{2}|60[1-8])[ -]?\d{3}[ -]?\d{4}(?!\d)")
DOCUMENT_CONTEXT = re.compile(
    r"(?i)\b(?:c[ée]dula|c\.?\s?c\.?|documento|identificaci[óo]n|ti|t\.i\.|pasaporte|nit)"
    r"(?:\s+(?:de\s+ciudadan[íi]a|n[úu]mero|no\.?|#|es|:))*\s*[:#]?\s*"
    r"(?P<value>(?:\d{1,3}(?:[.\s]\d{3}){1,3}|\d{5,11})(?:-\d)?)(?!\d)"
)
NIT = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-\d(?!\d)")
ACCOUNT_CONTEXT = re.compile(
    r"(?i)\b(?:cuenta|cta\.?|n[úu]mero\s+de\s+cuenta|account)"
    r"(?:\s+(?:de\s+)?(?:ahorros|corriente|n[úu]mero|no\.?|#|es|:))*\s*[:#]?\s*"
    r"(?P<value>\d[\d -]{6,22}\d)"
)
LONG_DIGITS = re.compile(r"(?<![\d\[])\d{9,}(?!\d)")

RULES: tuple[Rule, ...] = (
    Rule(SensitiveDataType.EMAIL, EMAIL),
    Rule(SensitiveDataType.TARJETA, CARD, accept=luhn_valid),
    Rule(SensitiveDataType.TELEFONO, PHONE),
    Rule(SensitiveDataType.DOCUMENTO, DOCUMENT_CONTEXT, group="value"),
    Rule(SensitiveDataType.DOCUMENTO, NIT),
    Rule(SensitiveDataType.CUENTA, ACCOUNT_CONTEXT, group="value"),
    Rule(SensitiveDataType.CUENTA, LONG_DIGITS),
)


class RuleBasedDetector:
    def __init__(self, rules: Sequence[Rule] = RULES) -> None:
        self._rules = rules

    def detect(self, text: str) -> Sequence[Detection]:
        taken: list[tuple[int, int]] = []
        detections: list[Detection] = []
        for rule in self._rules:
            for match in rule.pattern.finditer(text):
                start, end = match.span(rule.group)
                value = match.group(rule.group).strip()
                if not value:
                    continue
                if rule.accept is not None and not rule.accept(value):
                    continue
                if any(start < t_end and t_start < end for t_start, t_end in taken):
                    continue
                taken.append((start, end))
                detections.append(Detection(rule.kind, start, end))
        return sorted(detections, key=lambda d: d.start)
