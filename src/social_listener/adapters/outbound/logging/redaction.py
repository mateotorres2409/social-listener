"""Filtro de logs que redacta secretos conocidos y patrones de credenciales."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[REDACTADO]"

CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|secret|password|passwd|client[_-]?secret)\s*[=:]\s*)"
        r"[^\s,;&]+"
    ),
    re.compile(r"(?i)(x-api-key:\s*)\S+"),
    re.compile(r"(://[^:/\s]+:)[^@\s]+(@)"),
)


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = sorted({s for s in secrets if len(s) >= 4}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in CREDENTIAL_PATTERNS:
            text = pattern.sub(
                lambda m: m.group(1) + REDACTED + (m.group(2) if m.lastindex == 2 else ""),
                text,
            )
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redact(message)
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = self.redact(formatter.formatException(record.exc_info))
            record.exc_info = None
        record.msg = redacted
        record.args = None
        return True


def install_redaction(
    secrets: Iterable[str], logger: logging.Logger | None = None
) -> RedactingFilter:
    """Instala el filtro en los handlers del logger (raíz por defecto)."""
    target = logger or logging.getLogger()
    redacting = RedactingFilter(secrets)
    for handler in target.handlers:
        handler.addFilter(redacting)
    target.addFilter(redacting)
    return redacting
