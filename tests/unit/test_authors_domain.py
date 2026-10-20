from dataclasses import replace
from datetime import timedelta

from social_listener.domain.authors.policies import (
    accumulate_sentiment,
    is_recurrent,
    recurrence_window_start,
    register_mention,
    thread_root_candidate,
)
from social_listener.domain.mentions.model import AuthorSnapshot
from social_listener.domain.projects.model import RecurrenceRule
from social_listener.domain.shared.types import AuthorId, ProjectId
from tests.contracts.persistence import T0, incoming

P = ProjectId("p")


def data(external_id: str = "m-1", username: str = "juanp", days: int = 0, **kw: object):  # type: ignore[no-untyped-def]
    base = incoming(external_id, published_at=T0 + timedelta(days=days)).data
    return replace(base, author=AuthorSnapshot("a-1", username, followers=10), **kw)


def test_primera_mencion_crea_el_perfil() -> None:
    author = register_mention(
        None,
        new_id=AuthorId("a"),
        project_id=P,
        data=data(),
        mention_type="queja",
        sentiment_score=-0.8,
        is_official=False,
    )
    assert author.total_mentions == 1
    assert author.type_counts == {"queja": 1}
    assert author.sentiment_trend == -0.8
    assert author.first_seen_at == author.last_seen_at == T0


def test_cambio_de_username_conserva_el_perfil_y_guarda_el_anterior() -> None:
    first = register_mention(
        None,
        new_id=AuthorId("a"),
        project_id=P,
        data=data(),
        mention_type=None,
        sentiment_score=None,
        is_official=False,
    )
    second = register_mention(
        first,
        new_id=AuthorId("otro"),
        project_id=P,
        data=data("m-2", "juan_perez", days=1),
        mention_type="felicitacion",
        sentiment_score=0.9,
        is_official=False,
    )
    assert second.id == "a"
    assert second.username == "juan_perez"
    assert [c.username for c in second.previous_usernames] == ["juanp"]
    assert second.total_mentions == 2
    assert second.type_counts == {"felicitacion": 1}


def test_mencion_antigua_no_pisa_los_datos_vigentes() -> None:
    current = register_mention(
        None,
        new_id=AuthorId("a"),
        project_id=P,
        data=data(username="nuevo", days=5),
        mention_type=None,
        sentiment_score=None,
        is_official=False,
    )
    old = register_mention(
        current,
        new_id=AuthorId("a"),
        project_id=P,
        data=data("m-0", "viejo", days=0),
        mention_type=None,
        sentiment_score=None,
        is_official=False,
    )
    assert old.username == "nuevo"
    assert old.first_seen_at == T0


def test_sentimiento_acumulado_pondera_lo_reciente() -> None:
    trend = accumulate_sentiment(None, 0.8)
    trend = accumulate_sentiment(trend, -1.0)
    assert trend is not None
    assert -0.3 < trend < 0.5
    assert accumulate_sentiment(trend, None) == trend


def test_autor_recurrente() -> None:
    rule = RecurrenceRule(threshold=3, window_days=30)
    assert not is_recurrent(3, rule)
    assert is_recurrent(4, rule)
    assert recurrence_window_start(T0, rule) == T0 - timedelta(days=30)


def test_raiz_provisional_del_hilo() -> None:
    assert thread_root_candidate(data()) == "m-1"
    assert thread_root_candidate(data("m-2", parent_external_id="m-1")) == "m-1"
