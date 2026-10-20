# Esquema de exportación OpenTelemetry

La plataforma entrega cada mención enriquecida como un **LogRecord OTLP** al OpenTelemetry
Collector de la entidad, que lo enruta a los backends que elija (Elasticsearch/OpenSearch,
Loki, Datadog, Splunk, ClickHouse…). Versión del esquema: **1.0.0**
(atributo `social.schema.version`).

## Envío

- **Proceso:** `social-listener dispatch` lee el outbox (`export_outbox`) y llama a
  `OTLPLogExporter.export()`. Un registro solo se marca como enviado si el Collector confirma
  (`SUCCESS`); si no, se reintenta con backoff y sobrevive a reinicios.
- **Entrega al menos una vez:** los consumidores deben **deduplicar por `social.mention.id`**
  (y por event name, ya que una misma mención puede tener `enriched` y luego `updated`).
- **Configuración:** variables estándar de OpenTelemetry: `OTEL_EXPORTER_OTLP_ENDPOINT`,
  `OTEL_EXPORTER_OTLP_PROTOCOL` (`http/protobuf` por defecto, o `grpc`),
  `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_EXPORTER_OTLP_CERTIFICATE` y `OTEL_RESOURCE_ATTRIBUTES`.
- **Recurso:** `service.name=social-listener`, `service.version` y `deployment.environment.name`.
- **Correlación:** cada LogRecord lleva el `trace_id` y el `span_id` del procesamiento de la
  mención en el pipeline (un span por etapa).

## Eventos

| Event name | Cuándo | Body |
|---|---|---|
| `social.mention.enriched` | La mención completó el pipeline | Texto enmascarado |
| `social.mention.updated` | Un analista corrigió la clasificación (`social.update.reason`, `social.update.dimensions`) | Texto enmascarado |
| `social.author.identity.resolved` | Un autor `pendiente` obtuvo un resultado definitivo | Mensaje corto con el estado |

## Severidad

| `social.impact` | Severidad |
|---|---|
| `bajo` | INFO |
| `medio` | INFO2 |
| `alto` | WARN |
| `critico` | ERROR |

Así, los backends pueden alertar con reglas estándar sobre WARN y ERROR.

## Atributos de una mención

| Atributo | Tipo | Descripción |
|---|---|---|
| `social.schema.version` | string | Versión semántica de este esquema |
| `social.project.id` | string | Proyecto (cliente) |
| `social.mention.id` | string | Id estable de la mención; clave de deduplicación |
| `social.mention.url` | string | URL pública de la mención |
| `social.mention.lang` | string | Idioma (vacío si se desconoce) |
| `social.mention.created_at` | string | Fecha de publicación (ISO 8601) |
| `social.listening_query.ids` | string[] | Tópicos de escucha que la capturaron |
| `social.mention.sensitive_data_types` | string[] | Tipos de dato sensible detectados (nunca los valores) |
| `social.review.required` | bool | Marcada para revisión humana |
| `social.channel` | string | Canal |
| `social.channel.format` | string | Formato: publicación, comentario, respuesta, reseña |
| `social.impact` | string | `bajo`, `medio`, `alto`, `critico` |
| `social.type` | string | Tipo (PQRS, alerta de fraude, riesgo reputacional, …) |
| `social.sentiment.label` | string | `positivo`, `neutral`, `negativo`, `mixto` |
| `social.sentiment.score` | double | Puntaje entre -1 y 1 |
| `social.category` | string | Categoría de la taxonomía |
| `social.subcategory` | string | Subcategoría (vacío si no aplica) |
| `social.topic` | string | Temática normalizada |
| `social.channel.source` | string | Origen del canal: `regla`, `llm`, `respaldo`, `manual` |
| `social.channel.confidence` | double | Confianza del canal |
| `social.impact.source` | string | Origen del impacto |
| `social.impact.confidence` | double | Confianza del impacto |
| `social.type.source` | string | Origen del tipo |
| `social.type.confidence` | double | Confianza del tipo |
| `social.sentiment.source` | string | Origen del sentimiento |
| `social.sentiment.confidence` | double | Confianza del sentimiento |
| `social.category.source` | string | Origen de la categoría |
| `social.category.confidence` | double | Confianza de la categoría |
| `social.topic.source` | string | Origen de la temática |
| `social.topic.confidence` | double | Confianza de la temática |
| `social.classifier.version` | string | Versión del clasificador |
| `social.classifier.provider` | string | Proveedor del modelo de lenguaje, si intervino uno |
| `social.classifier.model` | string | Modelo de lenguaje, si intervino uno |
| `social.taxonomy.version` | int | Versión de la taxonomía usada |
| `social.author.pseudonym_id` | string | Seudónimo estable del autor (HMAC) |
| `social.author.recurrent` | bool | Autor con quejas o reclamos recurrentes |
| `social.thread.id` | string | Hilo de conversación (vacío si no aplica) |
| `social.identity.status` | string | `identificado`, `no_identificado`, `ambiguo`, `pendiente`, `error` |
| `social.identity.customer_pseudonym` | string | Seudónimo del cliente; **solo** con `identificado` |

Atributos que **nunca** se exportan: texto original, id externo, username o nombre del autor y
referencia del cliente en claro. Una prueba de contrato lo verifica en cada cambio.

## Versionado

- Agregar un atributo: versión menor (`1.1.0`).
- Eliminar o renombrar un atributo, o cambiar su tipo o significado: versión mayor (`2.0.0`),
  con notas de migración para los consumidores en el mismo cambio.
- El contrato vive en `src/social_listener/adapters/outbound/otel/schema.py`; esta tabla debe
  coincidir con él (también lo verifica una prueba).
