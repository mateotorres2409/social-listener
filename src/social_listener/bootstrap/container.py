"""Raíz de composición: arma el grafo de dependencias según el entorno."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine

from social_listener import __version__
from social_listener.adapters.inbound.worker.instrumentation import OtelInstrumentation
from social_listener.adapters.inbound.worker.runner import Instrumentation
from social_listener.adapters.outbound.clock import SystemClock
from social_listener.adapters.outbound.identity.http_client import (
    ClientCredentials,
    HttpIdentityService,
    mtls_client,
)
from social_listener.adapters.outbound.logging.redaction import install_redaction
from social_listener.adapters.outbound.otel.metrics import (
    BusinessMetrics,
    MeteredExport,
    MeteredIdentity,
    MeteredProtection,
)
from social_listener.adapters.outbound.otel.telemetry import (
    SERVICE_NAME,
    Telemetry,
    current_trace_context,
    setup_telemetry,
)
from social_listener.adapters.outbound.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
    create_engine,
)
from social_listener.adapters.outbound.privacy.pseudonymizer import HmacPseudonymizer
from social_listener.adapters.outbound.privacy.rule_detector import RuleBasedDetector
from social_listener.adapters.outbound.secrets.provider import (
    EnvSecretProvider,
    LayeredSecretProvider,
    SecretProvider,
    StaticSecretProvider,
)
from social_listener.application.ports.inbound.stages import StageHandler
from social_listener.application.ports.outbound.services import (
    Clock,
    IdentityService,
    Pseudonymizer,
    SensitiveDataDetector,
    SourceConnector,
)
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory
from social_listener.application.use_cases.authors.identity import (
    RetryIdentityValidation,
    RevalidateIdentity,
    ValidateIdentity,
)
from social_listener.application.use_cases.authors.individualize import IndividualizeAuthor
from social_listener.application.use_cases.classification.classify import ClassifyMention
from social_listener.application.use_cases.classification.manage import (
    ConfigureModelChain,
    CorrectClassification,
    ManageTaxonomy,
)
from social_listener.application.use_cases.export.assemble import EnrichedMentionAssembler
from social_listener.application.use_cases.export.export import ExportMention
from social_listener.application.use_cases.ingestion.ingest import (
    IngestMentions,
    ListDeadLetters,
    ReprocessDeadLetter,
)
from social_listener.application.use_cases.ingestion.queries import ManageListeningQueries
from social_listener.application.use_cases.mentions import ViewAuthors, ViewMentions
from social_listener.application.use_cases.privacy.protect import ProtectMention
from social_listener.application.use_cases.privacy.retention import (
    ApplyRetention,
    SuppressAuthorData,
)
from social_listener.application.use_cases.projects import ManageProjects
from social_listener.bootstrap.connectors import build_connectors
from social_listener.bootstrap.models import ProjectModelChains, ProviderRegistry, default_registry
from social_listener.bootstrap.settings import Settings, load_settings

PSEUDONYMIZATION_KEY_REF = "pseudonymization-key"


@dataclass
class Container:
    settings: Settings
    clock: Clock
    telemetry: Telemetry
    engine: AsyncEngine
    uow_factory: UnitOfWorkFactory
    instrumentation: Instrumentation
    secrets: SecretProvider
    pseudonymizer: Pseudonymizer
    detector: SensitiveDataDetector
    connectors: dict[str, SourceConnector] = field(default_factory=dict)
    registry: ProviderRegistry | None = None
    _model_chains: ProjectModelChains | None = None
    _identity: IdentityService | None = None
    _metrics: BusinessMetrics | None = None

    def metrics(self) -> BusinessMetrics:
        if self._metrics is None:
            self._metrics = BusinessMetrics(self.telemetry.meter_provider.get_meter(SERVICE_NAME))
        return self._metrics

    # Casos de uso -----------------------------------------------------------

    def manage_projects(self) -> ManageProjects:
        return ManageProjects(self.uow_factory)

    def manage_queries(self) -> ManageListeningQueries:
        return ManageListeningQueries(self.uow_factory, known_connectors=self.connectors)

    def ingest_mentions(self) -> IngestMentions:
        return IngestMentions(self.uow_factory, self.connectors, self.clock)

    def list_dead_letters(self) -> ListDeadLetters:
        return ListDeadLetters(self.uow_factory)

    def reprocess_dead_letter(self) -> ReprocessDeadLetter:
        return ReprocessDeadLetter(self.uow_factory, self.clock)

    def manage_taxonomy(self) -> ManageTaxonomy:
        return ManageTaxonomy(self.uow_factory)

    def assembler(self) -> EnrichedMentionAssembler:
        return EnrichedMentionAssembler(self.pseudonymizer)

    def correct_classification(self) -> CorrectClassification:
        return CorrectClassification(self.uow_factory, self.assembler(), self.clock)

    def view_mentions(self) -> ViewMentions:
        return ViewMentions(self.uow_factory)

    def model_chains(self) -> ProjectModelChains:
        if self._model_chains is None:
            registry = self.registry or default_registry(self.secrets)
            self._model_chains = ProjectModelChains(
                self.uow_factory, registry, observer=self.metrics().on_chain_link
            )
        return self._model_chains

    def configure_model_chain(self) -> ConfigureModelChain:
        return ConfigureModelChain(
            self.uow_factory, self.registry or default_registry(self.secrets)
        )

    def classify_mention(self) -> ClassifyMention:
        return ClassifyMention(self.model_chains(), observer=self.metrics().on_classification)

    def identity_service(self) -> IdentityService:
        """Una sola instancia por proceso: su circuit breaker se comparte entre trabajos."""
        if self._identity is None:
            s = self.settings
            credentials = None
            if s.identity_token_url and s.identity_client_id and s.identity_client_secret:
                credentials = ClientCredentials(
                    s.identity_token_url,
                    s.identity_client_id,
                    s.identity_client_secret.get_secret_value(),
                )
            client = (
                mtls_client(s.identity_client_cert, s.identity_client_key, s.identity_ca_file)
                if s.identity_client_cert and s.identity_client_key
                else None
            )
            self._identity = HttpIdentityService(
                s.identity_service_url,
                client=client,
                credentials=credentials,
                timeout_s=s.identity_timeout_s,
            )
        return self._identity

    def validate_identity(self) -> StageHandler:
        return MeteredIdentity(
            ValidateIdentity(self.identity_service(), self.pseudonymizer, self.clock),
            self.metrics(),
        )

    def retry_identity(self) -> RetryIdentityValidation:
        return RetryIdentityValidation(
            self.identity_service(),
            self.pseudonymizer,
            self.clock,
            max_attempts=self.settings.stage_max_attempts,
        )

    def revalidate_identity(self) -> RevalidateIdentity:
        return RevalidateIdentity(self.uow_factory, self.clock)

    def view_authors(self) -> ViewAuthors:
        return ViewAuthors(self.uow_factory)

    def individualize_author(self) -> IndividualizeAuthor:
        return IndividualizeAuthor(self.clock)

    def export_mention(self) -> StageHandler:
        return MeteredExport(ExportMention(self.assembler(), self.clock), self.metrics())

    def protect_mention(self) -> StageHandler:
        return MeteredProtection(ProtectMention(self.detector), self.metrics())

    def apply_retention(self) -> ApplyRetention:
        return ApplyRetention(self.uow_factory, self.clock)

    def suppress_author(self) -> SuppressAuthorData:
        return SuppressAuthorData(self.uow_factory, self.pseudonymizer, self.clock)

    async def aclose(self) -> None:
        await self.engine.dispose()
        self.telemetry.shutdown()


def secret_values(settings: Settings) -> list[str]:
    """Valores que nunca deben aparecer en los logs de la aplicación."""
    values = [
        settings.database_url.get_secret_value(),
        settings.pseudonymization_key.get_secret_value(),
        settings.api_token.get_secret_value(),
    ]
    if settings.identity_client_secret is not None:
        values.append(settings.identity_client_secret.get_secret_value())
    return values


def build_container(
    settings: Settings | None = None,
    *,
    telemetry: Telemetry | None = None,
    clock: Clock | None = None,
) -> Container:
    settings = settings or load_settings()
    install_redaction(secret_values(settings))
    clock = clock or SystemClock()
    telemetry = telemetry or setup_telemetry(
        service_version=__version__, environment=settings.environment
    )
    engine = create_engine(settings.database_url.get_secret_value())
    uow_factory = SqlAlchemyUnitOfWorkFactory(
        engine,
        clock,
        max_attempts=settings.stage_max_attempts,
        trace_context=current_trace_context,
    )
    secrets = LayeredSecretProvider(
        StaticSecretProvider(
            {PSEUDONYMIZATION_KEY_REF: settings.pseudonymization_key.get_secret_value()}
        ),
        EnvSecretProvider(),
    )
    instrumentation = OtelInstrumentation(
        telemetry.tracer(), telemetry.meter_provider.get_meter(SERVICE_NAME)
    )
    return Container(
        settings=settings,
        clock=clock,
        telemetry=telemetry,
        engine=engine,
        uow_factory=uow_factory,
        instrumentation=instrumentation,
        secrets=secrets,
        pseudonymizer=HmacPseudonymizer(
            secrets, key_ref=PSEUDONYMIZATION_KEY_REF, key_id=settings.pseudonymization_key_id
        ),
        detector=RuleBasedDetector(),
        connectors=build_connectors(settings),
    )
