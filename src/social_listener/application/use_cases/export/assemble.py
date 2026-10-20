"""Arma la mención enriquecida exportable a partir de lo persistido."""

from __future__ import annotations

from social_listener.application.dto.export import EnrichedMention
from social_listener.application.ports.inbound.stages import PermanentStageError
from social_listener.application.ports.outbound.services import Pseudonymizer
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from social_listener.application.use_cases.classification.classify import CLASSIFIER_VERSION
from social_listener.domain.identity.model import IdentityStatus
from social_listener.domain.mentions.model import Mention
from social_listener.domain.shared.types import MentionId

AUTHOR_NAMESPACE = "autor"
CUSTOMER_NAMESPACE = "cliente"


def author_pseudonym_of(pseudonymizer: Pseudonymizer, channel: str, external_id: str) -> str:
    return pseudonymizer.pseudonymize(AUTHOR_NAMESPACE, f"{channel}:{external_id}")


def customer_pseudonym_of(pseudonymizer: Pseudonymizer, customer_ref: str) -> str:
    return pseudonymizer.pseudonymize(CUSTOMER_NAMESPACE, customer_ref)


class EnrichedMentionAssembler:
    def __init__(self, pseudonymizer: Pseudonymizer) -> None:
        self._pseudonymizer = pseudonymizer

    async def assemble(self, uow: UnitOfWork, mention_id: MentionId) -> EnrichedMention:
        mention = await uow.mentions.get(mention_id)
        if mention is None or mention.text is None:
            raise PermanentStageError("la mención no existe o no pasó por protección")
        classification = await uow.classifications.get(mention_id)
        if classification is None:
            raise PermanentStageError("la mención no está clasificada")

        status = IdentityStatus.PENDIENTE
        customer_pseudonym: str | None = None
        recurrent = False
        author_id = await uow.mentions.author_of(mention_id)
        if author_id is not None:
            author = await uow.authors.get(author_id)
            recurrent = bool(author and author.recurrent)
            result = await uow.identity_results.get(author_id)
            if result is not None:
                status = result.status
                if result.status is IdentityStatus.IDENTIFICADO and result.customer_ref:
                    customer_pseudonym = customer_pseudonym_of(
                        self._pseudonymizer, result.customer_ref
                    )
        thread_id = await uow.mentions.thread_of(mention_id)
        return EnrichedMention(
            mention_id=mention.id,
            project_id=mention.project_id,
            url=mention.data.url,
            language=mention.data.language,
            created_at=mention.data.published_at,
            channel=mention.data.channel.value,
            channel_format=classification.channel_format,
            masked_text=mention.text.value,
            dimensions=dict(classification.values),
            sentiment_score=classification.sentiment_score,
            subcategory=classification.subcategory,
            taxonomy_version=classification.taxonomy_version,
            classifier_version=CLASSIFIER_VERSION,
            author_pseudonym_id=self._author_pseudonym(mention),
            author_recurrent=recurrent,
            thread_id=thread_id,
            identity_status=status,
            listening_query_ids=sorted(mention.query_ids),
            sensitive_data_types=sorted(t.value for t in mention.text.detected),
            customer_pseudonym=customer_pseudonym,
            needs_review=classification.needs_review,
        )

    def _author_pseudonym(self, mention: Mention) -> str:
        author = mention.data.author
        return author_pseudonym_of(
            self._pseudonymizer, mention.data.channel.value, author.external_id
        )
