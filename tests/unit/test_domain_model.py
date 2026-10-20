from datetime import UTC, datetime

import pytest

from social_listener.domain.authors.model import Author
from social_listener.domain.classification.model import (
    INDETERMINADO,
    Category,
    ClassificationSource,
    Dimension,
    DimensionValue,
    MentionClassification,
    Sentiment,
    SentimentLabel,
    Subcategory,
    Taxonomy,
)
from social_listener.domain.identity.model import IdentityResult, IdentityStatus
from social_listener.domain.mentions.model import (
    AuthorSnapshot,
    Channel,
    Engagement,
    IncomingMention,
    ListeningQuery,
    MentionData,
)
from social_listener.domain.privacy.model import MaskedText, SensitiveDataType
from social_listener.domain.shared.errors import InvalidDomainValueError
from social_listener.domain.shared.types import (
    AuthorId,
    ListeningQueryId,
    MentionId,
    ProjectId,
)

AHORA = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
AUTOR = AuthorSnapshot(external_id="123", username="juanp", followers=10)


def _datos(**cambios: object) -> MentionData:
    base: dict[str, object] = {
        "channel": Channel.X,
        "external_id": "m-1",
        "url": "https://x.com/juanp/status/1",
        "language": "es",
        "published_at": AHORA,
        "author": AUTOR,
    }
    base.update(cambios)
    return MentionData(**base)  # type: ignore[arg-type]


def _valor(valor: str = "queja", **cambios: object) -> DimensionValue:
    base: dict[str, object] = {
        "value": valor,
        "source": ClassificationSource.REGLA,
        "confidence": 0.9,
        "classifier_name": "reglas",
        "classifier_version": "1",
    }
    base.update(cambios)
    return DimensionValue(**base)  # type: ignore[arg-type]


class TestMencion:
    def test_mencion_valida(self) -> None:
        incoming = IncomingMention(data=_datos(), raw_text="hola")
        assert incoming.data.engagement.interactions == 0

    def test_requiere_id_externo(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            _datos(external_id=" ")

    def test_requiere_texto(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            IncomingMention(data=_datos(), raw_text="  ")

    def test_fecha_sin_zona_horaria(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            _datos(published_at=datetime(2026, 1, 1))

    def test_interacciones_negativas(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            Engagement(likes=-1)

    def test_interacciones_suman(self) -> None:
        assert Engagement(likes=3, shares=2, replies=1, views=100).interactions == 6

    def test_autor_sin_username(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            AuthorSnapshot(external_id="1", username="")


class TestTopicoDeEscucha:
    def test_valido(self) -> None:
        q = ListeningQuery(
            id=ListeningQueryId("q1"),
            project_id=ProjectId("p1"),
            name="Marca",
            channels=("mock",),
            terms=("banco",),
        )
        assert q.is_active

    def test_sin_criterios(self) -> None:
        with pytest.raises(InvalidDomainValueError, match="término, cuenta o hashtag"):
            ListeningQuery(
                id=ListeningQueryId("q1"),
                project_id=ProjectId("p1"),
                name="Marca",
                channels=("mock",),
            )

    def test_sin_canales(self) -> None:
        with pytest.raises(InvalidDomainValueError, match="canal"):
            ListeningQuery(
                id=ListeningQueryId("q1"),
                project_id=ProjectId("p1"),
                name="Marca",
                channels=(),
                terms=("banco",),
            )


class TestClasificacion:
    @pytest.mark.parametrize("puntaje", [-1.0, 0.0, 1.0])
    def test_puntaje_en_rango_para_mixto(self, puntaje: float) -> None:
        Sentiment(SentimentLabel.MIXTO, puntaje)

    @pytest.mark.parametrize("puntaje", [-1.01, 1.01])
    def test_puntaje_fuera_de_rango(self, puntaje: float) -> None:
        with pytest.raises(InvalidDomainValueError):
            Sentiment(SentimentLabel.MIXTO, puntaje)

    @pytest.mark.parametrize(
        ("etiqueta", "puntaje"),
        [
            (SentimentLabel.POSITIVO, -0.2),
            (SentimentLabel.NEGATIVO, 0.4),
            (SentimentLabel.NEUTRAL, 0.9),
        ],
    )
    def test_etiqueta_incoherente_con_puntaje(
        self, etiqueta: SentimentLabel, puntaje: float
    ) -> None:
        with pytest.raises(InvalidDomainValueError):
            Sentiment(etiqueta, puntaje)

    @pytest.mark.parametrize("confianza", [-0.1, 1.1])
    def test_confianza_fuera_de_rango(self, confianza: float) -> None:
        with pytest.raises(InvalidDomainValueError):
            _valor(confidence=confianza)

    def test_origen_llm_requiere_proveedor_y_modelo(self) -> None:
        with pytest.raises(InvalidDomainValueError, match="proveedor y modelo"):
            _valor(source=ClassificationSource.LLM)
        valor = _valor(source=ClassificationSource.LLM, provider="ollama", model="qwen3:4b")
        assert valor.provider == "ollama"

    def test_clasificacion_requiere_seis_dimensiones(self) -> None:
        valores = {d: _valor() for d in Dimension if d is not Dimension.TEMATICA}
        with pytest.raises(InvalidDomainValueError, match="tematica"):
            MentionClassification(
                mention_id=MentionId("m"),
                values=valores,
                taxonomy_version=1,
                channel_format="publicacion",
                sentiment_score=0.0,
            )

    def test_indeterminado(self) -> None:
        assert _valor(INDETERMINADO).is_undetermined


class TestTaxonomia:
    def test_contiene_categoria_y_subcategoria(self) -> None:
        tax = Taxonomy(
            version=1,
            categories=(Category("tarjetas", "Tarjetas", (Subcategory("credito", "Crédito"),)),),
        )
        assert tax.contains("tarjetas", "credito")
        assert not tax.contains("tarjetas", "debito")
        assert not tax.contains("seguros")

    def test_nueva_version(self) -> None:
        tax = Taxonomy(version=1, categories=(Category("a", "A"),))
        assert tax.next_version((Category("b", "B"),)).version == 2

    def test_codigos_duplicados(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            Taxonomy(version=1, categories=(Category("a", "A"), Category("a", "A2")))


class TestPrivacidad:
    def test_texto_enmascarado_con_marcador(self) -> None:
        texto = MaskedText("tarjeta [TARJETA]", frozenset({SensitiveDataType.TARJETA}))
        assert texto.sensitive_exposure

    def test_marcador_faltante(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            MaskedText("sin marcador", frozenset({SensitiveDataType.EMAIL}))


class TestAutorEIdentidad:
    def test_autor_fechas_incoherentes(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            Author(
                id=AuthorId("a"),
                project_id=ProjectId("p"),
                channel=Channel.X,
                external_id="1",
                username="u",
                first_seen_at=AHORA,
                last_seen_at=AHORA.replace(year=2025),
            )

    def test_identificado_requiere_referencia(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            IdentityResult(
                author_id=AuthorId("a"),
                status=IdentityStatus.IDENTIFICADO,
                validated_at=AHORA,
                username="u",
            )

    def test_no_identificado_no_lleva_referencia(self) -> None:
        with pytest.raises(InvalidDomainValueError):
            IdentityResult(
                author_id=AuthorId("a"),
                status=IdentityStatus.NO_IDENTIFICADO,
                validated_at=AHORA,
                username="u",
                customer_ref="ref",
            )

    def test_estados_definitivos(self) -> None:
        assert IdentityStatus.AMBIGUO.is_definitive
        assert not IdentityStatus.PENDIENTE.is_definitive
