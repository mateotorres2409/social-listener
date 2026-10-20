"""Contrato de los atributos ``social.*`` que la plataforma exporta por OpenTelemetry.

Cualquier cambio incompatible (eliminar o renombrar un atributo) incrementa la versión
mayor de ``SCHEMA_VERSION``. Los consumidores deben deduplicar por ``social.mention.id``
(la entrega es al menos una vez).
"""

from __future__ import annotations

from collections.abc import Mapping

from opentelemetry._logs import SeverityNumber

SCHEMA_VERSION = "1.0.0"

# Eventos
MENTION_ENRICHED = "social.mention.enriched"
MENTION_UPDATED = "social.mention.updated"
AUTHOR_IDENTITY_RESOLVED = "social.author.identity.resolved"

# Atributos comunes
SCHEMA = "social.schema.version"
PROJECT_ID = "social.project.id"

# Mención
MENTION_ID = "social.mention.id"
MENTION_URL = "social.mention.url"
MENTION_LANG = "social.mention.lang"
MENTION_CREATED_AT = "social.mention.created_at"
LISTENING_QUERY_IDS = "social.listening_query.ids"
SENSITIVE_DATA_TYPES = "social.mention.sensitive_data_types"
REVIEW_REQUIRED = "social.review.required"

# Dimensiones (valor; su origen y confianza van en ``<atributo>.source`` y ``.confidence``)
CHANNEL = "social.channel"
CHANNEL_FORMAT = "social.channel.format"
IMPACT = "social.impact"
TYPE = "social.type"
SENTIMENT_LABEL = "social.sentiment.label"
SENTIMENT_SCORE = "social.sentiment.score"
CATEGORY = "social.category"
SUBCATEGORY = "social.subcategory"
TOPIC = "social.topic"

DIMENSION_ATTRIBUTES: Mapping[str, str] = {
    "canal": CHANNEL,
    "impacto": IMPACT,
    "tipo": TYPE,
    "sentimiento": "social.sentiment",
    "categoria": CATEGORY,
    "tematica": TOPIC,
}
"""Dimensión del dominio → prefijo de sus atributos ``.source`` y ``.confidence``."""

# Clasificador
CLASSIFIER_VERSION = "social.classifier.version"
CLASSIFIER_PROVIDER = "social.classifier.provider"
CLASSIFIER_MODEL = "social.classifier.model"
TAXONOMY_VERSION = "social.taxonomy.version"

# Autor, hilo e identidad
AUTHOR_PSEUDONYM_ID = "social.author.pseudonym_id"
AUTHOR_RECURRENT = "social.author.recurrent"
THREAD_ID = "social.thread.id"
IDENTITY_STATUS = "social.identity.status"
IDENTITY_CUSTOMER_PSEUDONYM = "social.identity.customer_pseudonym"

REQUIRED_MENTION_ATTRIBUTES: tuple[str, ...] = (
    SCHEMA,
    MENTION_ID,
    MENTION_URL,
    MENTION_LANG,
    MENTION_CREATED_AT,
    CHANNEL,
    CHANNEL_FORMAT,
    IMPACT,
    TYPE,
    SENTIMENT_LABEL,
    SENTIMENT_SCORE,
    CATEGORY,
    SUBCATEGORY,
    TOPIC,
    AUTHOR_PSEUDONYM_ID,
    AUTHOR_RECURRENT,
    THREAD_ID,
    IDENTITY_STATUS,
    PROJECT_ID,
    LISTENING_QUERY_IDS,
    CLASSIFIER_VERSION,
    TAXONOMY_VERSION,
    *(
        f"{prefix}.{suffix}"
        for prefix in DIMENSION_ATTRIBUTES.values()
        for suffix in ("source", "confidence")
    ),
)

FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "social.mention.raw_text",
        "social.mention.text",
        "social.author.external_id",
        "social.author.username",
        "social.author.display_name",
        "social.identity.customer_ref",
    }
)
"""Nunca deben aparecer: texto original, identificadores del autor o del cliente en claro."""

SEVERITY_BY_IMPACT: Mapping[str, tuple[SeverityNumber, str]] = {
    "bajo": (SeverityNumber.INFO, "INFO"),
    "medio": (SeverityNumber.INFO2, "INFO2"),
    "alto": (SeverityNumber.WARN, "WARN"),
    "critico": (SeverityNumber.ERROR, "ERROR"),
}

OPTIONAL_MENTION_ATTRIBUTES: tuple[str, ...] = (
    SENSITIVE_DATA_TYPES,
    REVIEW_REQUIRED,
    CLASSIFIER_PROVIDER,
    CLASSIFIER_MODEL,
    IDENTITY_CUSTOMER_PSEUDONYM,
)
"""Presentes según el caso (p. ej. el seudónimo de cliente solo con ``identificado``)."""

ALL_MENTION_ATTRIBUTES: frozenset[str] = frozenset(
    REQUIRED_MENTION_ATTRIBUTES + OPTIONAL_MENTION_ATTRIBUTES
)
