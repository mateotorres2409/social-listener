# Tasks

## 1. Base del proyecto y regla de dependencias

- [x] 1.1 Crear `pyproject.toml` con uv (Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, asyncpg, HTTPX, SDK de OpenTelemetry con exportador OTLP, SDK de Anthropic, SDK de OpenAI; dev: pytest, pytest-asyncio, testcontainers, ruff, mypy, import-linter) y verificar que `uv sync` termina sin errores
- [x] 1.2 Crear la estructura de capas de D1 (`domain/`, `application/ports/{inbound,outbound}/`, `application/use_cases/`, `application/dto/`, `adapters/{inbound,outbound}/`, `bootstrap/`) con los subpaquetes por capacidad, más `stubs/`, `contracts/`, `deploy/` y `tests/{unit,fakes,integration,e2e}/`. Verificar que `uv run python -c "import social_listener"` funciona
- [x] 1.3 Configurar los contratos de import-linter (capas `adapters → application → domain`; `domain` sin dependencias de terceros; `application` sin dependencias de `adapters` ni de SDKs de infraestructura) y verificar que `uv run lint-imports` pasa, y que falla al agregar en una rama de prueba un `import sqlalchemy` en `domain/`
- [x] 1.4 Configurar ruff, mypy (estricto en `domain/` y `application/`), pytest y un pipeline de CI que ejecute los cuatro (ruff, mypy, lint-imports, pytest), y verificar que corre en verde sobre el proyecto vacío
- [x] 1.5 Implementar en `bootstrap/` la carga de configuración desde el entorno con Pydantic Settings (secretos aparte, sin valores por defecto para claves) y el contenedor de dependencias vacío, y verificar con una prueba unitaria que falta una clave obligatoria produce un error claro sin mostrar valores

## 2. Núcleo de dominio y puertos base

- [x] 2.1 Implementar en `domain/` las entidades y value objects (Mention, Channel, Author, UsernameHistory, Thread, ListeningQuery, Classification por dimensión, Taxonomy versionada, Topic, IdentityResult, MaskedText) como dataclasses inmutables con validación propia, y los enums de las seis dimensiones y de los estados de identidad. Verificar con pruebas unitarias la validación de campos obligatorios y de rangos (puntaje -1..1, confianza 0..1)
- [x] 2.2 Definir como `Protocol` en `application/ports/outbound/` los puertos de D1 (repositorios, `UnitOfWork`, `StageQueue`, `CheckpointStore`, `SourceConnector`, `SensitiveDataDetector`, `Pseudonymizer`, `LLMClassifier`, `IdentityService`, `EnrichedMentionOutbox`, `AuditLog`, `Clock`) y verificar que `mypy` y `lint-imports` pasan
- [x] 2.3 Implementar en `tests/fakes/` los fakes en memoria de cada puerto y una batería de pruebas de contrato por puerto, parametrizable por implementación. Verificar que los fakes la pasan

## 3. Persistencia, cola de etapas y runner

- [x] 3.1 Crear en `adapters/outbound/persistence/` los modelos SQLAlchemy y las migraciones Alembic para projects, listening_queries, connector_checkpoints, mentions (con `raw_text` y `masked_text` separados y restricción única `(channel, external_id)`), mention_queries, authors, author_username_history, threads, classifications, taxonomies, topics, identity_results, pipeline_jobs, dead_letters, export_outbox y audit_log. Verificar que `alembic upgrade head` y `downgrade base` funcionan en un PostgreSQL de testcontainers
- [x] 3.2 Implementar los repositorios y el `UnitOfWork` de PostgreSQL con sus mapeadores entidad ↔ modelo, y verificar que pasan la batería de contratos de 2.3 contra testcontainers, incluido que una inserción duplicada de `(channel, external_id)` devuelve la Mención existente
- [x] 3.3 Implementar el adaptador de `StageQueue` con `FOR UPDATE SKIP LOCKED`, reintentos con backoff y paso a `dead_letters`, y verificar con pruebas de integración que dos workers concurrentes no procesan el mismo job y que un job con los reintentos agotados termina en dead letters
- [x] 3.4 Declarar en `application` la tabla de transiciones de etapas e implementar en `adapters/inbound/worker/` el runner `worker --stage <etapa>`, que ejecuta el caso de uso de la etapa dentro de un `UnitOfWork` y encola la siguiente. Verificar con una prueba que una etapa fallida no encola la siguiente
- [x] 3.5 Implementar el setup de telemetría en `adapters/outbound/otel/` (recurso `service.name`, `service.version`, `deployment.environment.name`; TracerProvider y MeterProvider OTLP configurados por `OTEL_EXPORTER_OTLP_*`), la instrumentación del runner (un span por etapa, latencia y errores) y su cableado en `bootstrap`. Verificar con un receptor OTLP en memoria que el span de una etapa llega con los atributos de recurso

## 4. Ingesta de menciones

- [x] 4.1 Implementar los casos de uso `ManageListeningQueries`, `IngestMentions` (checkpoints, normalización al modelo canónico, deduplicación, asociación a varios tópicos, menciones inválidas a dead letters) y `ReprocessDeadLetter`. Verificar con fakes los escenarios de `ingesta-menciones`: tópico válido, tópico sin criterios, mención completa, campos ausentes, mención inválida, misma mención en dos tópicos, reingesta y reanudación desde el checkpoint
- [x] 4.2 Implementar en el caso de uso el backoff con jitter ante límites de tasa del puerto `SourceConnector`, sin avanzar el checkpoint de lotes fallidos, y verificar con un conector falso que devuelve límites de tasa que no se pierden ni duplican menciones
- [x] 4.3 Implementar los adaptadores `mock` y `fixture` de `SourceConnector` y verificar que pasan su batería de contratos y que `fixture` entrega exactamente las menciones del archivo, en orden
- [x] 4.4 Implementar los routers FastAPI de tópicos de escucha y de dead letters (autenticación por token de servicio en el adaptador HTTP) y verificar con pruebas de API los escenarios de tópico pausado sin captura y de reprocesamiento desde la etapa fallida
- [x] 4.5 Cablear en `bootstrap` la ingesta (conectores, repositorios y cola) y documentar en `docs/conectores.md` cómo agregar un adaptador de `SourceConnector`. Verificar que el ejemplo del documento corresponde al conector `mock`

## 5. Protección de datos

- [x] 5.1 Implementar en `domain/privacy` los tipos de dato sensible, `MaskedText` y la validación de Luhn, y verificar con pruebas unitarias los casos válidos e inválidos
- [x] 5.2 Implementar el adaptador por reglas de `SensitiveDataDetector` (tarjeta con Luhn, cuenta, cédula/NIT, celular, correo, con marcadores tipados) y verificar con un conjunto de casos en español, incluidos falsos positivos de 16 dígitos sin Luhn válido, que el texto enmascarado es el esperado
- [x] 5.3 Implementar el caso de uso `ProtectMention` (`masked_text`, tipos detectados, marca de exposición) y verificar con fakes los escenarios de `proteccion-datos`, y que ningún otro caso de uso accede a `raw_text`, con una prueba sobre la interfaz de los repositorios
- [x] 5.4 Implementar el adaptador HMAC-SHA256 de `Pseudonymizer` con `key_id` sobre `SecretProvider` y verificar que es estable para el mismo autor, distinto entre autores y que cambia al rotar la clave
- [x] 5.5 Implementar los casos de uso `ApplyRetention` y `SuppressAuthorData` (con `AuditLog` sin datos personales), su endpoint y el comando `worker --stage retention`, y verificar con fakes y con una prueba de integración los escenarios de mención vencida y de solicitud de supresión
- [x] 5.6 Agregar en los adaptadores un filtro de logs de aplicación que redacte secretos y verificar con una prueba que un error de autenticación simulado no imprime la credencial
- [x] 5.7 Cablear la protección en `bootstrap` y documentar en `docs/proteccion-datos.md` los datos detectados, la seudonimización, la rotación de claves y la retención. Verificar que los marcadores documentados coinciden con los implementados

## 6. Clasificación de menciones

- [x] 6.1 Implementar en `domain/classification` los catálogos de dimensiones, la taxonomía financiera por defecto versionada (tarjetas, cuentas, créditos, inversiones, seguros, canales digitales, oficinas y cajeros, atención al cliente, fraude y seguridad, tarifas y costos, reputación corporativa), las reglas de canal y formato, `ImpactPolicy`, la normalización de temáticas y las reglas de respaldo por léxicos en español. Verificar con pruebas unitarias los escenarios de respuesta en Instagram, influenciador con queja, alerta de fraude de bajo alcance, felicitación de bajo alcance y temática recurrente
- [x] 6.2 Implementar en `application` los DTOs `ClassificationRequest` y `ClassificationProposal` (con proveedor y modelo), las excepciones `ModelUnavailable` e `InvalidModelOutput` y el `ClassificationPromptBuilder` (instrucciones de sistema, contenido del usuario y JSON Schema de salida como estructura neutra). Verificar con pruebas unitarias que la salida es idéntica sin importar el proveedor destino y que solo contiene texto enmascarado y metadatos no identificables
- [x] 6.3 Implementar el caso de uso `ClassifyMention` (reglas → `LLMClassifier` → validación contra catálogo y taxonomía → respaldo → impacto → marca de revisión por umbral, con registro de proveedor y modelo) y verificar con un `LLMClassifier` falso los escenarios de valor fuera de la taxonomía, todos los modelos no disponibles, salida inválida y clasificación por un modelo
- [x] 6.4 Implementar el caso de uso `CorrectClassification` (origen `manual`, confianza 1, evento al `EnrichedMentionOutbox`) y los routers de taxonomía y corrección, y verificar con pruebas de API la corrección manual y que publicar una versión nueva de la taxonomía no altera las clasificaciones previas
- [x] 6.5 Implementar el adaptador `anthropic` de `LLMClassifier` (API directa, Bedrock y Vertex; salida estructurada; prompt caching del bloque de sistema; reintento de corrección). Consultar la skill `claude-api` antes de escribirlo. Verificar que pasa la batería de contratos del puerto con un transporte HTTP falso, incluidos timeout y salida inválida
- [x] 6.6 Implementar el adaptador `ollama` (API nativa `/api/chat` con HTTPX, `format` con el JSON Schema, temperatura 0, reintento de corrección) y verificar que pasa la batería de contratos con un transporte HTTP falso, y con una prueba opcional (marcada) contra Ollama real en testcontainers con un modelo pequeño
- [x] 6.7 Implementar el adaptador `openai_compatible` (SDK de OpenAI con `base_url`, `response_format` `json_schema` con degradación a `json_object` según la bandera de capacidades, reintento de corrección) y verificar que pasa la batería de contratos contra respuestas falsas de estilo OpenAI y vLLM
- [x] 6.8 Implementar el compuesto `fallback_chain` (timeout y circuit breaker por eslabón, primera propuesta válida) y el adaptador `null`, y verificar los escenarios de falla del proveedor primario, todos los modelos no disponibles y modelos de lenguaje desactivados
- [x] 6.9 Implementar en `bootstrap` el registro de proveedores y el armado y cacheo de la cadena por proyecto desde `model_chain` (credenciales por referencia al `SecretProvider`), y el endpoint para configurarla. Verificar con pruebas de API los escenarios de proyecto con modelo local, proyectos con proveedores distintos y proveedor no soportado
- [ ] 6.10 Crear un conjunto de evaluación con al menos 100 menciones financieras anonimizadas y etiquetadas, y un comando `uv run python -m social_listener.bootstrap.eval_classification --provider <p> --model <m>` que reporte la precisión por dimensión, la latencia y la tasa de salidas inválidas. Verificar que el comando corre al menos con un modelo de Ollama y deja en `docs/clasificacion.md` el reporte comparativo, la descripción de las dimensiones y los catálogos, y la guía para configurar y aprobar un proveedor o modelo nuevo

## 7. Individualización de autores

- [x] 7.1 Implementar en `domain/authors` las políticas de historial de username, métricas del perfil (sentimiento acumulado ponderado por recencia), recurrencia por umbral y ventana, y armado de hilos, y verificar con pruebas unitarias los escenarios de cambio de username, autor recurrente y respuesta que llega antes que el padre
- [x] 7.2 Implementar el caso de uso `IndividualizeAuthor` (resolución por canal + id externo, actualización del perfil, hilos, cuentas oficiales con hilo atendido y excluidas de la identidad) y verificar con fakes los escenarios de `individualizacion-autores`
- [x] 7.3 Implementar el router de consulta de autor e historial, cablear la etapa en `bootstrap` y verificar con una prueba de API que devuelve el perfil y las menciones ordenadas por fecha

## 8. Validación de identidad

- [x] 8.1 Escribir `contracts/identity-service.openapi.yaml` según D6 y verificar que valida con un linter de OpenAPI
- [x] 8.2 Implementar en `domain/identity` los estados y la política de TTL por estado, y los casos de uso `ValidateIdentity` y `RetryIdentityValidation` (caché, invalidación por username, `pendiente` ante `IdentityServiceUnavailable`, reintento con backoff, `error` revalidable, evento `social.author.identity.resolved`). Verificar con un `IdentityService` falso los escenarios de `validacion-identidad`
- [x] 8.3 Implementar el stub `stubs/identity/` (FastAPI, reglas por prefijo de username para `match`, `no_match`, `ambiguous`, lentitud y error) y verificar con pruebas que cumple el contrato OpenAPI
- [x] 8.4 Implementar el adaptador HTTPX de `IdentityService` (OAuth2 client_credentials o mTLS, timeout, circuit breaker, campos extra descartados) y verificar contra el stub que pasa la batería de contratos del puerto, que la solicitud solo lleva canal, id externo y username, y que con el circuit breaker abierto no llama al servicio
- [x] 8.5 Implementar el endpoint de revalidación manual, cablear la etapa en `bootstrap` y documentar en `docs/identidad.md` el contrato, los estados, el TTL y las credenciales. Verificar que los ejemplos del documento coinciden con el OpenAPI

## 9. Exportación OpenTelemetry

- [x] 9.1 Implementar el caso de uso `ExportMention`, que arma el DTO `EnrichedMention` (con `customer_pseudonym` solo con estado `identificado`) y lo entrega al `EnrichedMentionOutbox`, y verificar con fakes los escenarios de autor identificado y no identificado
- [x] 9.2 Definir `adapters/outbound/otel/schema.py` (constantes `social.*`, `SCHEMA_VERSION = "1.0.0"`, lista de atributos prohibidos) y una prueba de contrato que falle si falta un atributo requerido o aparece uno prohibido
- [x] 9.3 Implementar el adaptador OTLP del outbox: escritura transaccional en `export_outbox` con trace_id y span_id del contexto activo; mapeo a LogRecord (event names `social.mention.enriched`, `social.mention.updated` y `social.author.identity.resolved`, body enmascarado, origen y confianza por dimensión, versiones del clasificador y de la taxonomía, severidad por impacto); y despachador que marca como enviado solo con `SUCCESS` de `OTLPLogExporter.export()`. Verificar con el receptor OTLP en memoria los escenarios de mención crítica, Collector caído, reinicio con registros pendientes y eventos de actualización
- [x] 9.4 Implementar los decoradores de métricas de negocio sobre los casos de uso (`social.mentions.processed` por canal, tipo, sentimiento, categoría e impacto; identidad por estado; detecciones de datos sensibles por tipo; tokens, latencia y fallas por proveedor y modelo; uso de cada eslabón de la cadena) con lista blanca de atributos, aplicados en `bootstrap`. Verificar con una prueba que un atributo fuera de la lista blanca se rechaza
- [x] 9.5 Cablear el despachador como proceso en `bootstrap` y documentar en `docs/esquema-otel.md` los atributos, los event names, la severidad, la clave de deduplicación (`social.mention.id`) y la política de versionado. Verificar con una prueba que la tabla del documento coincide con `schema.py`

## 10. Entorno local e integración de punta a punta

- [ ] 10.1 Crear `deploy/docker-compose.yml` (PostgreSQL, API, workers por etapa, despachador del outbox, stub de identidad, Ollama con un modelo pequeño, OTel Collector, grafana/otel-lgtm) y `deploy/otel-collector.yaml`, y verificar que `docker compose up` deja todos los servicios saludables
- [ ] 10.2 Crear una prueba e2e que ingiera un fixture de menciones financieras (con tarjeta expuesta, alerta de fraude, influenciador, cliente identificado, autor ambiguo y respuesta de la cuenta oficial) con la cadena de modelos `ollama` → reglas, y afirme sobre los LogRecords recibidos por el Collector: seis dimensiones, proveedor y modelo del clasificador, enmascaramiento, estados de identidad, severidades y ausencia de atributos prohibidos
- [ ] 10.3 Escribir el `README.md` (propósito, arquitectura hexagonal con el diagrama de capas y puertos, arranque local, variables de entorno y enlaces a `docs/`) y verificar que siguiendo sus pasos en un entorno limpio la prueba e2e pasa

## Workflow follow-up

- Revisar los artefactos con las áreas de seguridad de la información y de cumplimiento de la entidad antes de aplicar el cambio.
- Archivar el cambio (`/opsx:archive`) cuando se cumplan los criterios de revisión del proyecto, y verificar que las specs se trasladaron a `openspec/specs/`.
