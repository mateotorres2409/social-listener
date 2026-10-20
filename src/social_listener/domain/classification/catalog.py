"""Catálogos de dimensiones y taxonomía financiera por defecto."""

from __future__ import annotations

from social_listener.domain.classification.model import (
    Category,
    ImpactLevel,
    MentionType,
    SentimentLabel,
    Subcategory,
    Taxonomy,
)

TYPE_VALUES = frozenset(t.value for t in MentionType)
SENTIMENT_VALUES = frozenset(s.value for s in SentimentLabel)
IMPACT_VALUES = frozenset(i.label for i in ImpactLevel)

TYPE_DEFINITIONS: dict[MentionType, str] = {
    MentionType.PETICION: "solicita a la entidad una acción o un trámite concreto",
    MentionType.QUEJA: "expresa inconformidad con la atención o el servicio",
    MentionType.RECLAMO: "exige una corrección o compensación por un cobro o un error",
    MentionType.SUGERENCIA: "propone una mejora del servicio",
    MentionType.FELICITACION: "reconoce o agradece un buen servicio",
    MentionType.CONSULTA: "pregunta por información, requisitos o funcionamiento",
    MentionType.ALERTA_FRAUDE: "reporta fraude, suplantación, phishing o robo",
    MentionType.RIESGO_REPUTACIONAL: "amenaza con denunciar, escalar a medios o reguladores",
    MentionType.INFORMATIVA: "menciona a la entidad sin pedir ni reclamar nada",
    MentionType.IRRELEVANTE: "no trata sobre la entidad o es spam",
}


def _sub(*pairs: tuple[str, str]) -> tuple[Subcategory, ...]:
    return tuple(Subcategory(code, name) for code, name in pairs)


DEFAULT_TAXONOMY = Taxonomy(
    version=1,
    categories=(
        Category(
            "tarjetas",
            "Tarjetas",
            _sub(("credito", "Tarjeta de crédito"), ("debito", "Tarjeta débito")),
        ),
        Category(
            "cuentas",
            "Cuentas",
            _sub(("ahorros", "Cuenta de ahorros"), ("corriente", "Cuenta corriente")),
        ),
        Category(
            "creditos",
            "Créditos",
            _sub(
                ("consumo", "Consumo y libre inversión"),
                ("vivienda", "Vivienda e hipotecario"),
                ("vehiculo", "Vehículo"),
            ),
        ),
        Category("inversiones", "Inversiones", _sub(("cdt", "CDT"), ("fondos", "Fondos"))),
        Category("seguros", "Seguros"),
        Category(
            "canales_digitales",
            "Canales digitales",
            _sub(("app", "App móvil"), ("web", "Banca en línea")),
        ),
        Category("oficinas_cajeros", "Oficinas y cajeros"),
        Category("atencion_cliente", "Atención al cliente"),
        Category("fraude_seguridad", "Fraude y seguridad"),
        Category("tarifas_costos", "Tarifas y costos"),
        Category("reputacion_corporativa", "Reputación corporativa"),
    ),
)
