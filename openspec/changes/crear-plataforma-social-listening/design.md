# Design

## Context

El repositorio está vacío (solo `README.md` y la configuración de OpenSpec): es un desarrollo greenfield. La motivación y el alcance están en `proposal.md` y los comportamientos exigidos en `specs/`. Restricciones que condicionan el diseño:

- **Cliente objetivo:** entidades financieras. Hay datos personales, cruce con la base de clientes y requisitos de auditoría, y la información que sale del perímetro (modelos externos y telemetría) debe minimizarse.
- **Stack acordado:** Python + FastAPI. Clasificación híbrida: reglas + modelos de lenguaje de varios proveedores (Claude, Ollama y APIs compatibles con OpenAI), sin depender de uno solo.
- **Arquitectura obligatoria:** Clean Architecture con puertos y adaptadores (hexagonal), con la regla de dependencias verificada en CI.
- **Microservicio de identidad:** todavía no existe, así que la plataforma define su contrato.
- **Destino de la exportación:** un OpenTelemetry Collector que opera la entidad. La plataforma no elige el backend final.

## Goals / Non-Goals

**Goals:**
- Un núcleo (`domain` + `application`) sin dependencias de frameworks ni de infraestructura, probado con puertos falsos.
- Aislar detrás de puertos todo lo que la entidad pueda cambiar: conectores, proveedores y modelos de lenguaje, microservicio de identidad, persistencia, cola de etapas y exportación.
- Cambiar de proveedor o de modelo de lenguaje, o agregar uno nuevo, es configuración o un adaptador nuevo, nunca un cambio en el núcleo.
- Un pipeline por etapas en el que cada etapa sea idempotente, reintentable y observable, y que no pierda menciones.
- Operar con la infraestructura mínima: PostgreSQL y el Collector.
- Un entorno local reproducible con Docker Compose para verificar de punta a punta.

**Non-Goals:**
- Conectores reales de redes sociales: cada uno será un cambio propio, implementando el puerto `SourceConnector`.
- Interfaz web o dashboards: la visualización se hace en los backends que reciben los datos por OTel.
- Alta disponibilidad multi-región y autoescalado: el diseño los permite, pero no se configuran en esta entrega.
- Integración con el IdP corporativo para la API de administración: en esta entrega se usa un token de servicio por entorno, implementado en el adaptador de entrada HTTP y reemplazable por OIDC sin tocar el núcleo.

## Decisions

### D1. Clean Architecture hexagonal en un monolito modular

Un solo paquete, `social_listener`, organizado por capas, con las capacidades como subpaquetes dentro de cada capa. Hay dos tipos de proceso, ambos adaptadores de entrada: `api` (FastAPI) y `worker --stage <etapa>`. Cada etapa escala de forma independiente lanzando más réplicas del worker.

```
src/social_listener/
  domain/                      # sin dependencias externas (solo biblioteca estándar)
    mentions/                  # Mention, Channel, MentionId, reglas de normalización
    privacy/                   # SensitiveDataType, MaskedText, validación de Luhn
    classification/            # dimensiones y catálogos, ImpactPolicy, reglas de respaldo,
                               # Taxonomy (versionada), Topic y su normalización
    authors/                   # Author, UsernameHistory, Thread, política de recurrencia
    identity/                  # IdentityResult, IdentityStatus, política de TTL
    shared/                    # errores de dominio, tipos base
  application/
    ports/
      inbound/                 # interfaces de los casos de uso
      outbound/                # SourceConnector, CheckpointStore, MentionRepository,
                               # AuthorRepository, ThreadRepository, TaxonomyRepository,
                               # IdentityResultRepository, StageQueue, UnitOfWork,
                               # SensitiveDataDetector, Pseudonymizer, LLMClassifier,
                               # IdentityService, EnrichedMentionOutbox, AuditLog, Clock
    use_cases/                 # ManageListeningQueries, IngestMentions, ProtectMention,
                               # ClassifyMention, CorrectClassification, IndividualizeAuthor,
                               # ValidateIdentity, RetryIdentityValidation, ExportMention,
                               # ApplyRetention, SuppressAuthorData, ReprocessDeadLetter
    dto/                       # comandos y resultados de los casos de uso
  adapters/
    inbound/
      http/                    # routers FastAPI, esquemas de request/response, auth
      worker/                  # runner de etapas, CLI, instrumentación OTel del pipeline
    outbound/
      persistence/             # SQLAlchemy + Alembic: repositorios, UnitOfWork, StageQueue
      connectors/              # mock, fixture (futuros: x, meta, ...)
      llm/                     # implementan LLMClassifier: anthropic (directo, Bedrock,
                               # Vertex), ollama (API nativa), openai_compatible (OpenAI,
                               # Azure OpenAI, vLLM, LM Studio, ...), fallback_chain, null
      identity/                # cliente HTTPX + circuit breaker (implementa IdentityService)
      privacy/                 # detector por reglas, seudonimizador HMAC
      otel/                    # outbox OTLP, mapeo a LogRecord, schema.py, métricas
      secrets/                 # SecretProvider por entorno o gestor de secretos
  bootstrap/                   # raíz de composición: settings, contenedor de dependencias,
                               # arranque de api y workers por entorno
stubs/identity/                # stub FastAPI del microservicio de identidad
contracts/identity-service.openapi.yaml
deploy/                        # docker-compose, configuración del Collector
tests/
  unit/                        # domain y casos de uso con puertos falsos
  fakes/                       # implementaciones en memoria de los puertos de salida
  integration/                 # adaptadores contra PostgreSQL, stub y receptor OTLP
  e2e/
```

**Regla de dependencias:** `adapters → application → domain`, y `bootstrap` puede depender de todo.
- `domain` solo usa la biblioteca estándar. No usa Pydantic: las entidades son dataclasses inmutables con validación propia.
- `application` depende solo de `domain`.
- Los modelos Pydantic, SQLAlchemy y los esquemas HTTP viven en `adapters`, que mapea entre ellos y las entidades del dominio.
- La regla se hace cumplir con `import-linter` (contratos `layers` e `independence`) en CI.

**Puertos de salida:** son `typing.Protocol`, para no acoplar los adaptadores a una herencia concreta.

**Casos de uso:** reciben sus puertos por constructor. `bootstrap` arma el grafo de dependencias según el entorno, por ejemplo la cadena de modelos de cada proyecto o solo reglas, y stub o servicio real de identidad.

*Alternativas consideradas:*
- *Capas tradicionales (controller/service/repository):* acoplan la lógica a SQLAlchemy y FastAPI y dificultan cambiar de proveedor de modelos, de servicio de identidad o de cola, que es lo que pedirá la entidad.
- *Un hexágono por capacidad:* cada capacidad con sus propias capas. Multiplica la ceremonia y vuelve más difíciles de expresar los contratos de imports con seis capacidades acopladas en un mismo pipeline.
- *Microservicios por etapa:* multiplican despliegues sin necesidad en el volumen inicial. Los límites por puertos permiten extraer servicios más adelante.

### D2. PostgreSQL como almacenamiento y como cola de trabajo, detrás de puertos

Los puertos de salida `StageQueue` y `UnitOfWork` tienen como adaptador PostgreSQL:
- Las etapas se encadenan con una tabla `pipeline_jobs` (mention_id, stage, status, attempts, next_attempt_at, last_error), y los workers toman trabajo con `SELECT … FOR UPDATE SKIP LOCKED`.
- Cada caso de uso trabaja dentro de un `UnitOfWork`: guarda su resultado y encola la siguiente etapa en la misma transacción, lo que da idempotencia y atomicidad sin coordinación distribuida.
- Cuando se agotan los reintentos, el trabajo pasa a `dead_letters`, y el caso de uso `ReprocessDeadLetter` puede reencolarlo (spec `ingesta-menciones`).

*Alternativas consideradas:*
- *Kafka:* da más throughput, pero agrega operación y no es transaccional con el almacenamiento.
- *Redis Streams:* agrega otra pieza de infraestructura y pierde durabilidad.

Si el volumen lo exige, migrar a Kafka es escribir otro adaptador de `StageQueue` (riesgo R1).

### D3. Orden de las etapas

`ingesta → normalización/dedup → protección → clasificación → individualización → identidad → exportación`

- La protección va antes de la clasificación porque a los modelos de lenguaje solo les llega el texto enmascarado.
- El impacto se calcula al final de la clasificación porque depende del tipo, el sentimiento y la categoría. El alcance (seguidores) ya viene en la Mención canónica.
- La identidad va después de la individualización porque se valida por autor, no por mención.

Cada etapa es un caso de uso en `application/use_cases/`. El orden lo define el runner de workers, en el adaptador de entrada, a partir de una tabla de transiciones declarada en `application`.

La deduplicación es una regla del caso de uso `IngestMentions`, y el adaptador de persistencia la garantiza con una restricción única sobre `(channel, external_id)` y `INSERT … ON CONFLICT`. La relación con los tópicos se guarda en `mention_queries`.

### D4. Clasificación híbrida

- **Reglas deterministas, en `domain/classification`:** el canal y el formato salen de la Mención canónica. El impacto lo calcula `ImpactPolicy`, una política pura con umbrales configurables por proyecto (seguidores, interacciones, ponderación por sentimiento y piso `alto` para `alerta_fraude` y `riesgo_reputacional`). Las reglas de respaldo (léxicos en español por categoría, tipo y sentimiento) también son políticas puras del dominio.
- **Puerto `LLMClassifier`, en `application/ports/outbound`:** recibe una `ClassificationRequest` y devuelve una `ClassificationProposal`, con un valor y una confianza por dimensión y el proveedor y modelo que respondieron. Si no puede, lanza `ModelUnavailable` o `InvalidModelOutput`, excepciones propias de `application`. El puerto no sabe de proveedores.
- **Prompt y esquema agnósticos del proveedor:** `ClassificationPromptBuilder`, en `application`, arma una sola vez las instrucciones de sistema (definiciones de dimensiones, taxonomía activa, catálogo de temáticas), el contenido del usuario (texto enmascarado y metadatos no identificables) y el esquema de salida, como estructura de datos neutra (JSON Schema en un `dict`). Cada adaptador solo traduce esa estructura al formato de su API. Así los proveedores reciben el mismo contexto y se validan contra el mismo esquema (spec "Clasificación uniforme entre proveedores").
- **Caso de uso `ClassifyMention`:**
  1. Aplica las reglas.
  2. Llama al puerto.
  3. Valida la propuesta contra los catálogos y la taxonomía; si hay valores inválidos, esa dimensión queda `indeterminado` y la mención se marca para revisión.
  4. Si el puerto falla, recurre al respaldo con origen `respaldo`.
  5. Calcula el impacto.
- **Adaptadores en `adapters/outbound/llm/`:**
  - `anthropic`: SDK de Anthropic con salida estructurada y *prompt caching* del bloque de sistema, porque cambia poco. Tiene variantes de cliente para la API directa, Amazon Bedrock y Google Vertex AI, según la región que apruebe la entidad (riesgo R2). Modelo sugerido: `claude-haiku-5-5` por costo y latencia, con `claude-sonnet-5-5` para menciones de alto alcance.
  - `ollama`: API nativa de Ollama (`/api/chat`) con HTTPX, el parámetro `format` con el JSON Schema y `options.temperature=0`. Permite clasificar dentro de la infraestructura de la entidad, sin que el texto salga del perímetro.
  - `openai_compatible`: SDK de OpenAI con `base_url` configurable y `response_format` de tipo `json_schema`. Si el servidor no lo soporta, degrada a `json_object` según una bandera de capacidad del modelo. Cubre OpenAI, Azure OpenAI, vLLM, LM Studio, Gemini mediante su endpoint compatible y otros.
  - Los tres validan la respuesta contra el esquema común y hacen un único reintento de corrección (le devuelven al modelo el error de validación) antes de lanzar `InvalidModelOutput`.
  - `fallback_chain`: un compuesto que implementa el mismo puerto, recorre la cadena configurada con el timeout de cada eslabón y devuelve la primera propuesta válida. Si todos fallan, lanza `ModelUnavailable`. Cada eslabón tiene su propio circuit breaker para no pagar el timeout de un proveedor caído en cada mención.
  - `null`: lanza siempre `ModelUnavailable`. Se usa cuando un proyecto desactiva los modelos de lenguaje.
- **Registro de proveedores, en `bootstrap`:** un mapa `nombre de proveedor → fábrica de adaptador`. La configuración por proyecto (`model_chain`: una lista de `{provider, model, base_url?, timeout_s, capabilities}`; credenciales por referencia al `SecretProvider`) se valida contra el registro. Un proveedor desconocido se rechaza (spec "Proveedor no soportado"). La cadena se arma por proyecto y se cachea hasta que cambie su configuración. Agregar un proveedor nuevo es escribir un adaptador y registrarlo.
- **Temáticas:** el caso de uso pasa al puerto las N temáticas más frecuentes del proyecto. La normalización a slug y la comparación por similitud con las existentes son reglas de `domain/classification`.
- **Versionado:** la taxonomía es una entidad versionada del dominio (`Taxonomy`), persistida por `TaxonomyRepository`, y cada clasificación referencia la versión con que se hizo.

*Alternativas consideradas:*
- *Solo modelos de lenguaje:* da menos control sobre impacto y canal y es más costoso.
- *LiteLLM como adaptador único:* cubre más de 100 proveedores con una sola dependencia. Se descarta porque es una dependencia grande con muchas transitivas (riesgo de cadena de suministro en una entidad financiera) y da menos control sobre la salida estructurada y el prompt caching de cada API. Con tres adaptadores se cubre la mayoría de los proveedores reales, porque casi todos exponen una API compatible con OpenAI. Si hiciera falta, LiteLLM puede agregarse después como un adaptador más del puerto.
- *Prompt propio de cada adaptador:* se descarta porque haría que proveedores distintos clasificaran con criterios distintos.

### D5. Protección de datos: políticas en el dominio, mecanismos en adaptadores

- **Dominio:** los tipos de dato sensible, el value object `MaskedText` y la validación de Luhn viven en `domain/privacy`.
- **Detección:** el puerto `SensitiveDataDetector` tiene un adaptador por reglas, en `adapters/outbound/privacy`, con expresiones regulares para cédula, NIT y cuenta según los formatos colombianos, celular `+57 3xx` y correo, más Luhn del dominio para tarjetas. Sus pruebas usan casos reales anonimizados.
  - *Alternativa:* Microsoft Presidio. Es más pesado y está orientado al inglés. Puede agregarse después como otro adaptador del mismo puerto.
- **Caso de uso `ProtectMention`:** llena `masked_text`, guarda los tipos detectados y marca la exposición de datos sensibles. El texto original se guarda en una columna aparte (`mentions.raw_text`). Los repositorios exponen al resto de los casos de uso solo el texto enmascarado; el acceso a `raw_text` queda restringido a `ProtectMention` y a la retención.
- **Seudonimización:** el puerto `Pseudonymizer` tiene un adaptador HMAC-SHA256 que obtiene la clave del `SecretProvider` y la identifica con un `key_id` para poder rotarla. Se seudonimizan `author_pseudonym_id` (a partir de canal + id externo) y `customer_pseudonym` (a partir de la referencia opaca de identidad).
- **Retención y supresión:** son los casos de uso `ApplyRetention` (que se ejecuta como `worker --stage retention`) y `SuppressAuthorData` (que se ejecuta en un `UnitOfWork` y registra la operación por el puerto `AuditLog`, sin datos personales).

### D6. Microservicio de identidad como puerto de salida

El contrato se publica en `contracts/identity-service.openapi.yaml`:

```
POST /v1/identity/lookups
Authorization: Bearer <token OAuth2 client_credentials>   (o mTLS)
{ "channel": "x", "external_author_id": "123", "username": "juanp" }
→ 200 { "status": "match" | "no_match" | "ambiguous",
        "customer_ref": "opaque-token" | null,
        "confidence": 0.0-1.0 }
```

- **Puerto `IdentityService`, en `application`:** recibe una consulta mínima (canal, id externo, username) y devuelve el estado, la referencia opaca y la confianza, o lanza `IdentityServiceUnavailable`, una excepción propia de `application`.
- **Adaptador, en `adapters/outbound/identity/`:** usa HTTPX asíncrono con timeout configurable (2 s por defecto) y OAuth2 client_credentials o mTLS. La respuesta se deserializa con un modelo que ignora los campos extra, así que nunca llegan al núcleo. El circuit breaker vive en el adaptador, porque es un mecanismo de resiliencia; cuando está abierto, el adaptador lanza `IdentityServiceUnavailable` sin llamar al servicio.
- **Caso de uso `ValidateIdentity`:** decide con las políticas de `domain/identity`.
  - Usa la caché de `IdentityResultRepository`, con un TTL por estado: `identificado` 30 d, `no_identificado` y `ambiguo` 7 d, `error` revalidable manualmente. La caché se invalida cuando cambia el username.
  - Excluye las cuentas oficiales.
  - Si el servicio no está disponible, la mención queda `pendiente`, sigue a exportación y se encola un `identity_retry`.
- **Caso de uso `RetryIdentityValidation`:** aplica backoff. Cuando el resultado es definitivo, emite `social.author.identity.resolved` por el puerto `EnrichedMentionOutbox`.
- **Stub:** `stubs/identity/`, en FastAPI, responde según reglas configurables por username (p. ej. prefijos `cliente_`, `ambiguo_`, `lento_`) para cubrir todos los estados en las pruebas de integración y e2e.

### D7. Exportación OTel durable: el outbox es un puerto y OTLP es un adaptador

La exportación de menciones es un **puerto de salida de negocio**. La instrumentación del pipeline es un **asunto transversal** de los adaptadores. Ninguna de las dos aparece en `domain`.

1. **Caso de uso `ExportMention`:** arma, dentro del `UnitOfWork`, un `EnrichedMention`, un DTO de `application` con la clasificación, el seudónimo del autor y el estado de identidad. Lo entrega al puerto `EnrichedMentionOutbox`. Las correcciones manuales y las resoluciones tardías usan el mismo puerto con sus propios tipos de evento.
2. **Adaptador OTLP, en `adapters/outbound/otel/`:**
   - Persiste el evento en `export_outbox` dentro de la misma transacción.
   - Toma el trace_id y el span_id del contexto OTel activo, que el runner de workers propaga.
   - Un despachador dedicado lee el outbox por lotes, mapea cada evento a LogRecord y llama directamente a `OTLPLogExporter.export()`. Solo lo marca como enviado si el resultado es `SUCCESS`; si falla, reintenta con backoff. No se usa `BatchLogRecordProcessor`, porque descarta registros si el Collector no responde.
   - El mapeo arma el event name, el body enmascarado, los atributos `social.*` y la severidad derivada del impacto.
3. **Contrato de los atributos:** está en `adapters/outbound/otel/schema.py` (constantes y `SCHEMA_VERSION = "1.0.0"`). Una prueba de contrato verifica que estén todos y que ningún atributo prohibido aparezca (texto original, id externo, username, `customer_ref`).
4. **Trazas y métricas del pipeline:** se instrumentan en los adaptadores de entrada (runner de workers y middleware FastAPI) y en los adaptadores de salida (HTTP, base de datos y modelos, con las instrumentaciones estándar de OTel). Usan el SDK estándar con exportadores OTLP por lotes, porque admiten pérdida ante fallos.
   - Las métricas de negocio (menciones por dimensión, resultados de identidad, detecciones de datos sensibles, tokens, latencia y fallas por proveedor y modelo, uso del respaldo de la cadena) se emiten desde decoradores de los casos de uso, que `bootstrap` aplica, y no desde el núcleo.
   - Solo usan atributos de baja cardinalidad, controlados por una lista blanca.
5. **Configuración:** se hace con las variables estándar `OTEL_EXPORTER_OTLP_*` y `OTEL_RESOURCE_ATTRIBUTES`.

Como los consumidores pueden recibir duplicados, deben deduplicar por `social.mention.id` (riesgo R4).

### D8. Estrategia de pruebas por capa y entorno local

- **Domain:** pruebas unitarias puras.
- **Casos de uso:** pruebas unitarias con los fakes en memoria de `tests/fakes/`, sin base de datos, red ni SDKs externos.
- **Adaptadores:** pruebas de integración propias:
  - Persistencia: PostgreSQL con testcontainers.
  - Identidad: el stub.
  - Modelos: un transporte HTTP falso por adaptador (Anthropic, Ollama, compatible con OpenAI) y una prueba opcional contra un Ollama real con un modelo pequeño.
  - OTLP: un receptor en memoria.
- **Contrato de los fakes:** una misma batería de pruebas de contrato por puerto se ejecuta contra el fake y contra el adaptador real, para garantizar que el fake se comporte igual.
- **e2e:** recorre el sistema cableado por `bootstrap` contra Docker Compose.

`deploy/docker-compose.yml` levanta:
- PostgreSQL.
- La API y los workers.
- El stub de identidad.
- Ollama con un modelo pequeño, para clasificar localmente sin credenciales externas.
- Un OpenTelemetry Collector (receptor OTLP → batch → exportadores `debug` y `otlp`).
- `grafana/otel-lgtm` para inspeccionar logs, métricas y trazas.

Las herramientas son `uv` para dependencias, `ruff` para lint y formato, `mypy` en modo estricto en `domain/` y `application/`, e `import-linter` para la regla de dependencias. Las cuatro corren en CI.

## Risks / Trade-offs

- **[R1] PostgreSQL como cola limita el throughput ante picos de crisis.** → Índices parciales sobre los jobs pendientes, lotes por worker, escalamiento horizontal con `SKIP LOCKED`. Migrar a Kafka es implementar otro adaptador de `StageQueue`. Se miden la latencia y la profundidad de cola por etapa con métricas OTel.
- **[R2] Enviar texto, aunque esté enmascarado, a un modelo externo puede no ser aceptable para el área de seguridad de la entidad.** → Solo sale texto enmascarado y sin identificadores. Cada proyecto elige su cadena: Ollama o vLLM en la infraestructura de la entidad para que nada salga del perímetro, Bedrock o Vertex en una región aprobada, o el adaptador nulo para operar solo con reglas.
- **[R3] El enmascaramiento por reglas puede dejar pasar datos sensibles con formatos no previstos.** → Conjunto de pruebas con casos reales anonimizados, métrica de detecciones por tipo y un puerto `SensitiveDataDetector` para agregar Presidio u otro detector.
- **[R4] La entrega al menos una vez genera duplicados aguas abajo.** → `social.mention.id` es estable y está documentado como clave de deduplicación para los consumidores.
- **[R5] El costo de los modelos externos crece con el volumen.** → Modelos pequeños o locales en la cadena, prompt caching del contexto estático donde el proveedor lo soporte, menciones `irrelevantes`/spam descartadas antes por reglas baratas, y métrica de tokens por proyecto, proveedor y modelo.
- **[R6] Un username de redes puede coincidir con un cliente equivocado (falso positivo de identidad).** → Se exporta solo la referencia seudonimizada y la confianza que da el microservicio. El estado `ambiguo` no produce referencia. La decisión de contactar al cliente queda fuera de la plataforma.
- **[R7] El contrato de identidad lo define la plataforma y puede no coincidir con el servicio real.** → Solo cambia el adaptador de `IdentityService`. El puerto, los casos de uso y la spec se mantienen mientras se conserven los estados.
- **[R8] La arquitectura hexagonal agrega código de mapeo y ceremonia (entidades, DTOs y modelos de persistencia separados).** → Los mapeadores son explícitos y solo están en los adaptadores; los puertos se definen como `Protocol` sin jerarquías, y no se crean puertos para lo que no tiene más de una implementación previsible, salvo los de infraestructura.
- **[R9] Un fake que diverge del adaptador real da pruebas verdes con un sistema roto.** → La misma batería de pruebas de contrato por puerto se ejecuta contra el fake y contra el adaptador (D8).
- **[R10] La calidad de la clasificación varía entre proveedores y modelos, y un respaldo silencioso de la cadena puede degradarla sin que nadie lo note.** → El conjunto de evaluación corre por cada combinación de proveedor y modelo, y su reporte sirve para aprobar un modelo antes de usarlo en un proyecto. Cada clasificación registra y exporta el proveedor y el modelo. Hay métricas de uso de cada eslabón de la cadena, para alertar cuando el respaldo se vuelve la norma.
- **[R11] Los modelos locales pequeños cumplen peor el esquema de salida.** → Salida forzada por JSON Schema (`format` en Ollama, `json_schema` en APIs compatibles), un reintento de corrección, validación contra catálogos y paso al siguiente eslabón. La bandera de capacidades por modelo evita pedir funciones que el servidor no soporta.

## Migration Plan

Es un sistema nuevo, sin datos previos que migrar.

1. Aplicar las migraciones Alembic sobre una base PostgreSQL vacía.
2. Desplegar la API, los workers por etapa y el despachador del outbox con las variables OTLP apuntando al Collector de la entidad.
3. Habilitar primero un proyecto con el conector `fixture` y verificar la recepción en el backend de destino; después, los conectores reales en cambios posteriores.
4. **Rollback:** detener los workers. El outbox y los jobs persistidos permiten reanudar sin pérdida al volver a una versión anterior.

## Open Questions

- Qué proveedores y modelos aprueba seguridad de la información para cada entidad, y en qué región: se resuelve configurando la cadena de cada proyecto (D4), sin cambios de código.
- Qué modelo local (servido con Ollama o vLLM) da la mejor relación entre calidad y costo en español financiero: lo determina el reporte de evaluación por modelo (tarea 6.10).
- Periodos de retención por defecto exigidos por el área legal de cada entidad: son parámetros por proyecto.
- Mecanismo de autenticación del microservicio de identidad real (OAuth2 o mTLS): el adaptador admite ambos, y se elige al integrar.
