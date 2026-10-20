"""Batería de contratos de persistencia.

Una subclase debe proveer los fixtures ``uow_factory`` (callable que devuelve un
``UnitOfWork``) y ``clock`` (``FixedClock`` que también usa la implementación).
La ejecutan tanto el fake en memoria como el adaptador PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from social_listener.application.dto.classification import ModelRoute
from social_listener.application.dto.pipeline import DeadLetter, Stage
from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.domain.authors.model import Author, Thread
from social_listener.domain.classification.model import (
    Category,
    ClassificationSource,
    Dimension,
    DimensionValue,
    MentionClassification,
    Taxonomy,
    Topic,
)
from social_listener.domain.identity.model import IdentityResult, IdentityStatus
from social_listener.domain.mentions.model import (
    AuthorSnapshot,
    Channel,
    IncomingMention,
    ListeningQuery,
    MentionData,
    QueryStatus,
)
from social_listener.domain.privacy.model import MaskedText, SensitiveDataType
from social_listener.domain.projects.model import Project
from social_listener.domain.shared.types import (
    AuthorId,
    ListeningQueryId,
    MentionId,
    ProjectId,
    ThreadId,
)
from tests.fakes.memory import FixedClock

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
P1 = ProjectId("11111111-1111-1111-1111-111111111111")
Q1 = ListeningQueryId("22222222-2222-2222-2222-222222222221")
Q2 = ListeningQueryId("22222222-2222-2222-2222-222222222222")


def incoming(external_id: str = "m-1", *, published_at: datetime = T0) -> IncomingMention:
    return IncomingMention(
        data=MentionData(
            channel=Channel.X,
            external_id=external_id,
            url=f"https://x.com/s/{external_id}",
            language="es",
            published_at=published_at,
            author=AuthorSnapshot(external_id="a-1", username="juanp", followers=5),
        ),
        raw_text=f"texto original de {external_id}",
    )


def author(author_id: str = "33333333-3333-3333-3333-333333333331", **kw: object) -> Author:
    base: dict[str, object] = {
        "id": AuthorId(author_id),
        "project_id": P1,
        "channel": Channel.X,
        "external_id": "a-1",
        "username": "juanp",
        "first_seen_at": T0,
        "last_seen_at": T0,
    }
    base.update(kw)
    return Author(**base)  # type: ignore[arg-type]


def classification(mention_id: MentionId, tipo: str = "queja") -> MentionClassification:
    def v(value: str) -> DimensionValue:
        return DimensionValue(value, ClassificationSource.REGLA, 1.0, "reglas", "1")

    values = {d: v("x") for d in Dimension} | {Dimension.TIPO: v(tipo)}
    return MentionClassification(
        mention_id=mention_id,
        values=values,
        taxonomy_version=1,
        channel_format="publicacion",
        sentiment_score=-0.5,
    )


class PersistenceContract:
    @pytest.fixture
    async def seeded(self, uow_factory: UnitOfWorkFactory) -> UnitOfWorkFactory:
        async with uow_factory() as uow:
            await uow.projects.save(Project(id=P1, name="Banco"))
            for qid in (Q1, Q2):
                await uow.queries.add(
                    ListeningQuery(
                        id=qid, project_id=P1, name=f"q{qid[-1]}", channels=("x",), terms=("b",)
                    )
                )
            await uow.commit()
        return uow_factory

    # Unidad de trabajo -------------------------------------------------------

    async def test_sin_commit_no_persiste(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            await uow.mentions.add_or_get(P1, incoming(), Q1)
        async with seeded() as uow:
            assert await uow.mentions.find_by_external(Channel.X, "m-1") is None

    async def test_excepcion_hace_rollback(self, seeded: UnitOfWorkFactory) -> None:
        async def falla_dentro_de_la_transaccion() -> None:
            async with seeded() as uow:
                await uow.mentions.add_or_get(P1, incoming(), Q1)
                raise RuntimeError("falla")

        with pytest.raises(RuntimeError):
            await falla_dentro_de_la_transaccion()
        async with seeded() as uow:
            assert await uow.mentions.find_by_external(Channel.X, "m-1") is None

    # Proyectos y cadenas de modelos -----------------------------------------

    async def test_proyecto_y_cadena_de_modelos(self, seeded: UnitOfWorkFactory) -> None:
        chain = (
            ModelRoute(provider="ollama", model="qwen3:4b", timeout_s=5),
            ModelRoute(provider="anthropic", model="claude-haiku-5-5", credential_ref="anth"),
            ModelRoute(
                provider="anthropic_bedrock",
                model="anthropic.claude-haiku-5-5",
                options={"region": "us-east-1"},
            ),
        )
        async with seeded() as uow:
            await uow.model_chains.save(P1, chain)
            await uow.commit()
        async with seeded() as uow:
            project = await uow.projects.get(P1)
            assert project is not None
            assert project.name == "Banco"
            assert tuple(await uow.model_chains.get(P1)) == chain

    # Tópicos y checkpoints ---------------------------------------------------

    async def test_topicos(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            q = await uow.queries.get(Q1)
            assert q is not None
            await uow.queries.save(
                ListeningQuery(
                    id=q.id,
                    project_id=q.project_id,
                    name=q.name,
                    channels=q.channels,
                    terms=q.terms,
                    status=QueryStatus.PAUSED,
                )
            )
            await uow.commit()
        async with seeded() as uow:
            assert [x.id for x in await uow.queries.list_active()] == [Q2]
            assert len(await uow.queries.list_by_project(P1)) == 2
            assert await uow.queries.delete(Q2)
            assert not await uow.queries.delete(Q2)

    async def test_checkpoints(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            assert await uow.checkpoints.get(Q1, "mock") is None
            await uow.checkpoints.save(Q1, "mock", "c1")
            await uow.checkpoints.save(Q1, "mock", "c2")
            await uow.commit()
        async with seeded() as uow:
            assert await uow.checkpoints.get(Q1, "mock") == "c2"
            assert await uow.checkpoints.get(Q1, "fixture") is None

    # Menciones -----------------------------------------------------------------

    async def test_deduplicacion_por_canal_e_id_externo(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            first, created = await uow.mentions.add_or_get(P1, incoming(), Q1)
            await uow.commit()
        async with seeded() as uow:
            second, created_again = await uow.mentions.add_or_get(P1, incoming(), Q2)
            await uow.commit()
        assert created
        assert not created_again
        assert second.id == first.id
        assert second.query_ids == {Q1, Q2}

    async def test_texto_original_y_proteccion(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            mention, _ = await uow.mentions.add_or_get(P1, incoming(), Q1)
            assert mention.text is None
            masked = MaskedText("tarjeta [TARJETA]", frozenset({SensitiveDataType.TARJETA}))
            await uow.mentions.save_protection(mention.id, masked)
            await uow.commit()
        async with seeded() as uow:
            stored = await uow.mentions.get(mention.id)
            assert stored is not None
            assert stored.text == masked
            assert await uow.mentions.get_raw_text(mention.id) == "texto original de m-1"

    async def test_autor_hilo_y_historial(self, seeded: UnitOfWorkFactory) -> None:
        a = author()
        t1 = Thread(ThreadId("44444444-4444-4444-4444-444444444441"), P1, Channel.X, "m-1")
        t2 = Thread(ThreadId("44444444-4444-4444-4444-444444444442"), P1, Channel.X, "m-9")
        async with seeded() as uow:
            await uow.authors.save(a)
            await uow.threads.save(t1)
            await uow.threads.save(t2)
            m1, _ = await uow.mentions.add_or_get(P1, incoming("m-1"), Q1)
            m2, _ = await uow.mentions.add_or_get(
                P1, incoming("m-2", published_at=T0 - timedelta(days=1)), Q1
            )
            await uow.mentions.assign_author(m1.id, a.id, t1.id)
            await uow.mentions.assign_author(m2.id, a.id, t2.id)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.mentions.author_of(m1.id) == a.id
            assert [m.id for m in await uow.mentions.list_by_author(a.id)] == [m2.id, m1.id]
            assert await uow.mentions.reassign_thread(t2.id, t1.id) == 1
            assert await uow.mentions.thread_of(m2.id) == t1.id

    async def test_purga_por_retencion(self, seeded: UnitOfWorkFactory) -> None:
        a = author()
        async with seeded() as uow:
            await uow.authors.save(a)
            old, _ = await uow.mentions.add_or_get(
                P1, incoming("viejo", published_at=T0 - timedelta(days=400)), Q1
            )
            new, _ = await uow.mentions.add_or_get(P1, incoming("nuevo"), Q1)
            await uow.mentions.assign_author(old.id, a.id, None)
            await uow.mentions.assign_author(new.id, a.id, None)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.mentions.purge_personal_data_before(P1, T0 - timedelta(days=365)) == 1
            await uow.commit()
        async with seeded() as uow:
            assert await uow.mentions.get_raw_text(old.id) is None
            assert await uow.mentions.author_of(old.id) is None
            purged = await uow.mentions.get(old.id)
            assert purged is not None
            assert purged.data.author.username == "suprimido"
            assert purged.data.author.followers is None
            assert await uow.mentions.get_raw_text(new.id) is not None
            assert await uow.mentions.purge_personal_data_of_author(a.id) == 1
            assert await uow.mentions.get_raw_text(new.id) is None

    # Clasificación, taxonomía y temáticas -------------------------------------

    async def test_clasificacion(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            mention, _ = await uow.mentions.add_or_get(P1, incoming(), Q1)
            await uow.taxonomies.publish(Taxonomy(1, (Category("a", "A"),)))
            await uow.classifications.save(classification(mention.id))
            await uow.commit()
        async with seeded() as uow:
            stored = await uow.classifications.get(mention.id)
            assert stored == classification(mention.id)

    async def test_taxonomia_por_defecto_y_versiones(self, seeded: UnitOfWorkFactory) -> None:
        default = Taxonomy(1, (Category("a", "A"),))
        async with seeded() as uow:
            assert await uow.taxonomies.get_active(P1) is None
            await uow.taxonomies.publish(default)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.taxonomies.get_active(P1) == default
            await uow.taxonomies.publish(Taxonomy(1, (Category("b", "B"),), project_id=P1))
            await uow.taxonomies.publish(Taxonomy(2, (Category("c", "C"),), project_id=P1))
            await uow.commit()
        async with seeded() as uow:
            active = await uow.taxonomies.get_active(P1)
            assert active is not None
            assert active.version == 2
            assert active.categories[0].code == "c"

    async def test_tematicas_mas_frecuentes(self, seeded: UnitOfWorkFactory) -> None:
        async with seeded() as uow:
            for slug, uses in (("caida-app", 3), ("cuota-manejo", 1)):
                for _ in range(uses):
                    await uow.topics.record_use(Topic(P1, slug, slug))
            await uow.commit()
        async with seeded() as uow:
            top = await uow.topics.most_frequent(P1, 5)
            assert [(t.slug, t.mentions_count) for t in top] == [
                ("caida-app", 3),
                ("cuota-manejo", 1),
            ]
            assert await uow.topics.get(P1, "otra") is None

    # Autores, hilos e identidad -------------------------------------------------

    async def test_autores_y_quejas(self, seeded: UnitOfWorkFactory) -> None:
        a = author(type_counts={"queja": 1}, sentiment_trend=-0.4)
        async with seeded() as uow:
            await uow.authors.save(a)
            m, _ = await uow.mentions.add_or_get(P1, incoming(), Q1)
            await uow.mentions.assign_author(m.id, a.id, None)
            await uow.taxonomies.publish(Taxonomy(1, (Category("a", "A"),)))
            await uow.classifications.save(classification(m.id, "queja"))
            await uow.commit()
        async with seeded() as uow:
            assert await uow.authors.get_by_external(P1, Channel.X, "a-1") == a
            assert await uow.authors.complaints_since(a.id, T0 - timedelta(days=1)) == 1
            assert await uow.authors.complaints_since(a.id, T0 + timedelta(days=1)) == 0

    async def test_eliminar_autores_inactivos(self, seeded: UnitOfWorkFactory) -> None:
        old = author(
            "33333333-3333-3333-3333-333333333332",
            external_id="a-2",
            first_seen_at=T0 - timedelta(days=500),
            last_seen_at=T0 - timedelta(days=400),
        )
        active = author()
        result = IdentityResult(
            author_id=old.id,
            status=IdentityStatus.NO_IDENTIFICADO,
            validated_at=T0,
            username="u",
        )
        async with seeded() as uow:
            await uow.authors.save(old)
            await uow.authors.save(active)
            await uow.identity_results.save(result)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.authors.delete_inactive_before(P1, T0 - timedelta(days=365)) == 1
            await uow.commit()
        async with seeded() as uow:
            assert await uow.authors.get(old.id) is None
            assert await uow.identity_results.get(old.id) is None
            assert await uow.authors.get(active.id) is not None

    async def test_hilos(self, seeded: UnitOfWorkFactory) -> None:
        t = Thread(ThreadId("44444444-4444-4444-4444-444444444441"), P1, Channel.X, "raiz")
        async with seeded() as uow:
            await uow.threads.save(t)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.threads.get_by_root(P1, Channel.X, "raiz") == t
            await uow.threads.delete(t.id)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.threads.get(t.id) is None

    async def test_resultado_de_identidad(self, seeded: UnitOfWorkFactory) -> None:
        a = author()
        result = IdentityResult(
            author_id=a.id,
            status=IdentityStatus.IDENTIFICADO,
            validated_at=T0,
            username="juanp",
            customer_ref="ref-1",
            confidence=0.9,
        )
        async with seeded() as uow:
            await uow.authors.save(a)
            await uow.identity_results.save(result)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.identity_results.get(a.id) == result
            assert await uow.identity_results.delete(a.id)

    # Cola de etapas y cola de errores -------------------------------------------

    async def test_encolar_es_idempotente_y_tomar_no_duplica(
        self, seeded: UnitOfWorkFactory
    ) -> None:
        async with seeded() as uow:
            await uow.jobs.enqueue(Stage.PROTECT, "s-1")
            await uow.jobs.enqueue(Stage.PROTECT, "s-1")
            await uow.commit()
        async with seeded() as uow:
            jobs = await uow.jobs.claim(Stage.PROTECT, 10)
            await uow.commit()
        async with seeded() as uow:
            again = await uow.jobs.claim(Stage.PROTECT, 10)
        assert [j.subject_id for j in jobs] == ["s-1"]
        assert again == []

    async def test_trabajo_futuro_no_disponible(
        self, seeded: UnitOfWorkFactory, clock: FixedClock
    ) -> None:
        async with seeded() as uow:
            await uow.jobs.enqueue(
                Stage.IDENTITY_RETRY, "a-1", available_at=clock.now() + timedelta(minutes=5)
            )
            await uow.commit()
        async with seeded() as uow:
            assert await uow.jobs.claim(Stage.IDENTITY_RETRY, 10) == []
        clock.advance(minutes=6)
        async with seeded() as uow:
            assert len(await uow.jobs.claim(Stage.IDENTITY_RETRY, 10)) == 1

    async def test_fallo_reintenta_y_luego_va_a_cola_de_errores(
        self, seeded: UnitOfWorkFactory, clock: FixedClock
    ) -> None:
        async with seeded() as uow:
            await uow.jobs.enqueue(Stage.CLASSIFY, "s-1", payload={"k": "v"})
            await uow.commit()
        async with seeded() as uow:
            (job,) = await uow.jobs.claim(Stage.CLASSIFY, 1)
            assert not await uow.jobs.fail(job, "transitorio", retry_at=clock.now())
            await uow.commit()
        async with seeded() as uow:
            (job,) = await uow.jobs.claim(Stage.CLASSIFY, 1)
            assert job.attempts == 1
            assert await uow.jobs.fail(job, "definitivo", retry_at=None)
            await uow.commit()
        async with seeded() as uow:
            (letter,) = await uow.dead_letters.list_recent(10)
            assert letter.stage == Stage.CLASSIFY
            assert letter.subject_id == "s-1"
            assert letter.reason == "definitivo"
            assert letter.attempts == 2
            assert dict(letter.payload) == {"k": "v"}
            assert await uow.jobs.claim(Stage.CLASSIFY, 1) == []

    async def test_cola_de_errores(self, seeded: UnitOfWorkFactory) -> None:
        letter = DeadLetter(
            id="55555555-5555-5555-5555-555555555551",
            stage=Stage.NORMALIZE,
            reason="sin id externo",
            attempts=1,
            created_at=T0,
            payload={"text": "hola"},
        )
        async with seeded() as uow:
            await uow.dead_letters.add(letter)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.dead_letters.get(letter.id) == letter
            await uow.dead_letters.remove(letter.id)
            await uow.commit()
        async with seeded() as uow:
            assert await uow.dead_letters.get(letter.id) is None

    async def test_auditoria(self, seeded: UnitOfWorkFactory) -> None:
        entry = AuditEntry("supresion", "k1:abc", T0, "api")
        async with seeded() as uow:
            await uow.audit.record(entry)
            await uow.commit()
        async with seeded() as uow:
            assert list(await uow.audit.list_recent(5)) == [entry]
