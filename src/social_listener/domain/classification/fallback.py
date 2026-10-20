"""Reglas de respaldo por léxicos en español.

Se usan cuando no hay modelo de lenguaje o falla. Son deliberadamente simples y
conservadoras: confianza baja, para que la mención quede marcada para revisión.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from social_listener.domain.classification.model import (
    INDETERMINADO,
    MentionType,
    Sentiment,
    SentimentLabel,
    Taxonomy,
)
from social_listener.domain.classification.rules import strip_accents

FALLBACK_CONFIDENCE = 0.4

TYPE_LEXICON: tuple[tuple[MentionType, tuple[str, ...]], ...] = (
    (
        MentionType.ALERTA_FRAUDE,
        (
            "fraude",
            "estafa",
            "suplant",
            "perfil falso",
            "pagina falsa",
            "phishing",
            "me robaron",
            "clonaron",
            "pidiendo claves",
            "pide claves",
            "hackearon",
        ),
    ),
    (
        MentionType.RIESGO_REPUTACIONAL,
        (
            "denunciar",
            "superfinanciera",
            "superintendencia",
            "demanda",
            "abogado",
            "medios",
            "noticia",
            "escandalo",
            "boicot",
        ),
    ),
    (
        MentionType.RECLAMO,
        (
            "exijo",
            "devolucion",
            "devuelvan",
            "reembolso",
            "no autorice",
            "cobro no autorizado",
            "reclamo",
            "cobraron doble",
            "me cobraron",
        ),
    ),
    (
        MentionType.QUEJA,
        (
            "pesimo",
            "terrible",
            "no funciona",
            "no sirve",
            "no me deja",
            "queja",
            "mal servicio",
            "se cayo",
            "caida",
            "horrible",
            "demora",
        ),
    ),
    (
        MentionType.SUGERENCIA,
        ("sugiero", "deberian", "seria bueno", "recomiendo que", "propongo", "amplie", "amplien"),
    ),
    (
        MentionType.FELICITACION,
        ("gracias", "excelente", "felicit", "muy bueno", "buen servicio", "me encanto", "facil"),
    ),
    (MentionType.PETICION, ("solicito", "necesito que", "requiero", "pido que")),
    (
        MentionType.CONSULTA,
        ("como ", "donde", "cual ", "cuales", "requisitos", "informacion", "?", "se puede"),
    ),
)

POSITIVE_WORDS = (
    "gracias",
    "excelente",
    "bueno",
    "buena",
    "felicit",
    "encanto",
    "rapido",
    "facil",
    "amable",
    "resolvieron",
    "genial",
)
NEGATIVE_WORDS = (
    "pesimo",
    "terrible",
    "malo",
    "mala",
    "no funciona",
    "no sirve",
    "no me deja",
    "cobraron",
    "fraude",
    "estafa",
    "robo",
    "robaron",
    "horrible",
    "demora",
    "exijo",
    "falso",
    "queja",
    "caida",
    "nunca",
)

CATEGORY_LEXICON: dict[str, tuple[str, ...]] = {
    "fraude_seguridad": (
        "fraude",
        "estafa",
        "suplant",
        "phishing",
        "perfil falso",
        "pagina falsa",
        "robo",
        "robaron",
        "clonaron",
        "hackearon",
    ),
    "tarifas_costos": ("cuota de manejo", "comision", "tarifa", "cobro", "cobraron", "interes"),
    "tarjetas": ("tarjeta", "cupo", "visa", "mastercard", "amex"),
    "creditos": ("credito", "prestamo", "hipoteca", "libre inversion", "leasing"),
    "cuentas": ("cuenta", "ahorros", "saldo", "transferencia", "consignacion"),
    "inversiones": ("cdt", "inversion", "fondo", "acciones"),
    "seguros": ("seguro", "poliza"),
    "canales_digitales": ("app", "aplicacion", "banca en linea", "sesion", "pagina web", "token"),
    "oficinas_cajeros": ("oficina", "sucursal", "cajero", "fila"),
    "atencion_cliente": ("atencion", "asesor", "linea", "call center", "chat", "servicio"),
    "reputacion_corporativa": ("marca", "imagen", "presidente", "escandalo", "publicidad"),
}


def _normalize(text: str) -> str:
    return " " + re.sub(r"\s+", " ", strip_accents(text).lower()) + " "


@dataclass(frozen=True, slots=True)
class FallbackResult:
    mention_type: MentionType
    sentiment: Sentiment
    category: str
    confidence: float = FALLBACK_CONFIDENCE


def fallback_type(text: str) -> MentionType:
    normalized = _normalize(text)
    for mention_type, keywords in TYPE_LEXICON:
        if any(k in normalized for k in keywords):
            return mention_type
    return MentionType.INFORMATIVA


def fallback_sentiment(text: str) -> Sentiment:
    normalized = _normalize(text)
    positive = sum(normalized.count(w) for w in POSITIVE_WORDS)
    negative = sum(normalized.count(w) for w in NEGATIVE_WORDS)
    if positive == negative == 0:
        return Sentiment(SentimentLabel.NEUTRAL, 0.0)
    score = (positive - negative) / (positive + negative)
    if positive and negative and abs(score) < 0.5:
        return Sentiment(SentimentLabel.MIXTO, score)
    if score > 0:
        return Sentiment(SentimentLabel.POSITIVO, round(min(1.0, 0.3 + 0.7 * score), 3))
    return Sentiment(SentimentLabel.NEGATIVO, round(max(-1.0, -0.3 + 0.7 * score), 3))


def fallback_category(text: str, taxonomy: Taxonomy) -> str:
    """Categoría de la taxonomía activa con más coincidencias de su léxico.

    En empate gana la de mayor prioridad del léxico (fraude y tarifas primero) y
    luego el orden de la taxonomía.
    """
    normalized = _normalize(text)
    priority = {code: i for i, code in enumerate(CATEGORY_LEXICON)}
    best: tuple[int, int, int, str] | None = None
    for position, category in enumerate(taxonomy.categories):
        keywords = CATEGORY_LEXICON.get(category.code) or tuple(
            strip_accents(w).lower() for w in category.name.split() if len(w) > 3
        )
        hits = sum(1 for k in keywords if k in normalized)
        if not hits:
            continue
        key = (-hits, priority.get(category.code, len(priority)), position, category.code)
        if best is None or key < best:
            best = key
    return best[3] if best else INDETERMINADO


def classify_with_fallback(text: str, taxonomy: Taxonomy) -> FallbackResult:
    return FallbackResult(
        mention_type=fallback_type(text),
        sentiment=fallback_sentiment(text),
        category=fallback_category(text, taxonomy),
    )
