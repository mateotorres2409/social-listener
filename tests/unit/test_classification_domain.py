"""Escenarios de ``clasificacion-menciones`` sobre las políticas del dominio."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from social_listener.domain.classification.catalog import (
    DEFAULT_TAXONOMY,
    TYPE_DEFINITIONS,
    TYPE_VALUES,
)
from social_listener.domain.classification.fallback import (
    classify_with_fallback,
    fallback_category,
    fallback_sentiment,
    fallback_type,
)
from social_listener.domain.classification.impact import ImpactThresholds
from social_listener.domain.classification.model import (
    INDETERMINADO,
    Category,
    ImpactLevel,
    MentionType,
    SentimentLabel,
    Taxonomy,
)
from social_listener.domain.classification.rules import (
    ImpactPolicy,
    channel_of,
    equivalent_topic,
    topic_slug,
)
from social_listener.domain.mentions.model import (
    AuthorSnapshot,
    Channel,
    ContentFormat,
    MentionData,
)

policy = ImpactPolicy(ImpactThresholds())


def test_respuesta_en_instagram() -> None:
    data = MentionData(
        channel=Channel.INSTAGRAM,
        external_id="c-2",
        url="https://instagram.com/p/1",
        language="es",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        author=AuthorSnapshot("a", "ana"),
        format=ContentFormat.RESPUESTA,
        parent_external_id="c-1",
    )
    assert channel_of(data) == ("instagram", "respuesta")


def test_taxonomia_por_defecto_cubre_las_categorias_de_la_spec() -> None:
    codes = {c.code for c in DEFAULT_TAXONOMY.categories}
    assert codes == {
        "tarjetas",
        "cuentas",
        "creditos",
        "inversiones",
        "seguros",
        "canales_digitales",
        "oficinas_cajeros",
        "atencion_cliente",
        "fraude_seguridad",
        "tarifas_costos",
        "reputacion_corporativa",
    }
    assert set(TYPE_DEFINITIONS) == {MentionType(v) for v in TYPE_VALUES}


class TestImpacto:
    def test_influenciador_con_queja(self) -> None:
        level = policy.evaluate(
            followers=50_000,
            interactions=20,
            sentiment=SentimentLabel.NEGATIVO,
            mention_type=MentionType.QUEJA,
            category="canales_digitales",
        )
        assert level >= ImpactLevel.ALTO

    def test_influenciador_con_queja_viral_es_critico(self) -> None:
        level = policy.evaluate(
            followers=50_000,
            interactions=900,
            sentiment=SentimentLabel.NEGATIVO,
            mention_type=MentionType.RECLAMO,
            category="tarifas_costos",
        )
        assert level is ImpactLevel.CRITICO

    def test_alerta_de_fraude_de_bajo_alcance(self) -> None:
        level = policy.evaluate(
            followers=12,
            interactions=0,
            sentiment=SentimentLabel.NEUTRAL,
            mention_type=MentionType.ALERTA_FRAUDE,
            category="fraude_seguridad",
        )
        assert level >= ImpactLevel.ALTO

    @pytest.mark.parametrize("tipo", [MentionType.ALERTA_FRAUDE, MentionType.RIESGO_REPUTACIONAL])
    def test_piso_alto_para_tipos_de_riesgo(self, tipo: MentionType) -> None:
        level = policy.evaluate(
            followers=None,
            interactions=0,
            sentiment=SentimentLabel.POSITIVO,
            mention_type=tipo,
            category=None,
        )
        assert level >= ImpactLevel.ALTO

    def test_felicitacion_de_bajo_alcance(self) -> None:
        level = policy.evaluate(
            followers=30,
            interactions=2,
            sentiment=SentimentLabel.POSITIVO,
            mention_type=MentionType.FELICITACION,
            category="atencion_cliente",
        )
        assert level is ImpactLevel.BAJO

    def test_umbrales_configurables(self) -> None:
        strict = ImpactPolicy(ImpactThresholds(influencer_followers=100, medium_followers=10))
        assert strict.reach(150, 0) is ImpactLevel.ALTO
        assert policy.reach(150, 0) is ImpactLevel.BAJO


class TestTematicas:
    def test_slug_normalizado(self) -> None:
        assert topic_slug("Caída de la App") == "caida-app"
        assert topic_slug("  Cobro de CUOTA de manejo!! ") == "cobro-cuota-manejo"

    def test_tematica_recurrente(self) -> None:
        existentes = ["caida-app", "cobro-cuota-manejo"]
        for propuesta in ("Caída de la app", "caida app movil", "la app caida"):
            assert equivalent_topic(topic_slug(propuesta), existentes) == "caida-app"

    def test_tematica_nueva(self) -> None:
        assert equivalent_topic("aumento-cupo", ["caida-app", "cobro-cuota-manejo"]) is None


class TestRespaldo:
    @pytest.mark.parametrize(
        ("texto", "tipo"),
        [
            (
                "me cobraron una cuota de manejo que no autoricé y exijo la devolución",
                MentionType.RECLAMO,
            ),
            (
                "hay un perfil falso que pide claves a nombre del banco",
                MentionType.ALERTA_FRAUDE,
            ),
            ("la app no me deja entrar, pésimo", MentionType.QUEJA),
            ("gracias por la atención, excelente", MentionType.FELICITACION),
            ("¿cómo abro una cuenta de ahorros?", MentionType.CONSULTA),
            ("sugiero que amplíen el horario", MentionType.SUGERENCIA),
            ("voy a denunciar ante la Superfinanciera", MentionType.RIESGO_REPUTACIONAL),
            ("hoy pasé por el centro", MentionType.INFORMATIVA),
        ],
    )
    def test_tipo(self, texto: str, tipo: MentionType) -> None:
        assert fallback_type(texto) is tipo

    def test_sentimiento(self) -> None:
        assert fallback_sentiment("la app es pésima, no funciona").label is SentimentLabel.NEGATIVO
        assert fallback_sentiment("excelente servicio, gracias").label is SentimentLabel.POSITIVO
        assert fallback_sentiment("hoy es martes").label is SentimentLabel.NEUTRAL

    def test_categoria(self) -> None:
        assert fallback_category("me cobraron la cuota de manejo", DEFAULT_TAXONOMY) == (
            "tarifas_costos"
        )
        assert fallback_category("no puedo iniciar sesión en la app", DEFAULT_TAXONOMY) == (
            "canales_digitales"
        )
        assert fallback_category("hoy es martes", DEFAULT_TAXONOMY) == INDETERMINADO

    def test_categoria_de_taxonomia_propia_por_nombre(self) -> None:
        propia = Taxonomy(1, (Category("nomina", "Pagos de nómina"),))
        assert fallback_category("no llegó el pago de la nómina", propia) == "nomina"

    def test_resultado_completo(self) -> None:
        result = classify_with_fallback("me robaron con una página falsa", DEFAULT_TAXONOMY)
        assert result.mention_type is MentionType.ALERTA_FRAUDE
        assert result.category == "fraude_seguridad"
        assert result.sentiment.label is SentimentLabel.NEGATIVO
        assert result.confidence < 0.6
