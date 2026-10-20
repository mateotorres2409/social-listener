# Spec Delta

## Purpose

Entregar cada mención enriquecida, las métricas agregadas y la telemetría del pipeline a cualquier backend compatible mediante OpenTelemetry (OTLP), con un esquema de atributos estable y versionado y sin exponer datos personales.

## ADDED Requirements

### Requirement: Una mención enriquecida por LogRecord
Cada mención que complete el pipeline SHALL emitirse como un LogRecord OTLP con event name `social.mention.enriched`, cuyo body es el texto enmascarado y cuyos atributos contienen la clasificación, el autor seudonimizado y el estado de identidad.

#### Scenario: Exportación de una mención
- **WHEN** una mención completa la clasificación, la individualización y la validación de identidad (o queda `pendiente`)
- **THEN** se emite exactamente un LogRecord `social.mention.enriched` hacia el endpoint OTLP configurado

### Requirement: Esquema de atributos versionado
Los atributos de dominio SHALL usar el namespace `social.*`, cubrir la mención, las seis dimensiones, el autor seudonimizado, el hilo, la identidad y el proyecto, e incluir `social.schema.version` con versionado semántico.

#### Scenario: Atributos presentes
- **WHEN** un consumidor recibe un LogRecord `social.mention.enriched`
- **THEN** encuentra al menos `social.schema.version`, `social.mention.id`, `social.mention.url`, `social.mention.lang`, `social.mention.created_at`, `social.channel`, `social.channel.format`, `social.impact`, `social.type`, `social.sentiment.label`, `social.sentiment.score`, `social.category`, `social.subcategory`, `social.topic`, `social.author.pseudonym_id`, `social.author.recurrent`, `social.thread.id`, `social.identity.status`, `social.project.id` y `social.listening_query.ids`

#### Scenario: Cambio incompatible del esquema
- **WHEN** se elimina o renombra un atributo existente
- **THEN** `social.schema.version` incrementa su versión mayor

### Requirement: Trazabilidad del clasificador en la exportación
Por cada dimensión, el LogRecord SHALL incluir su origen y su confianza (p. ej. `social.category.source`, `social.category.confidence`), así como `social.classifier.version` y `social.taxonomy.version`. Cuando intervino un modelo de lenguaje, MUST incluir `social.classifier.provider` y `social.classifier.model`.

#### Scenario: Dimensión clasificada por respaldo
- **WHEN** una dimensión se clasificó con las reglas de respaldo
- **THEN** su atributo de origen exportado es `respaldo`

#### Scenario: Clasificada por un modelo de respaldo de la cadena
- **WHEN** la mención la clasificó el segundo modelo de la cadena porque falló el primero
- **THEN** `social.classifier.provider` y `social.classifier.model` identifican al segundo modelo

### Requirement: Ausencia de datos personales en la telemetría
La telemetría exportada MUST NOT contener el texto original sin enmascarar, el id externo ni el username del autor, ni la referencia de cliente en claro. La referencia de cliente solo SHALL exportarse seudonimizada en `social.identity.customer_pseudonym`, y solo con estado `identificado`.

#### Scenario: Autor identificado
- **WHEN** se exporta la mención de un autor `identificado`
- **THEN** el LogRecord incluye `social.identity.status=identificado` y `social.identity.customer_pseudonym`, y no incluye la referencia de cliente original

#### Scenario: Autor no identificado
- **WHEN** se exporta la mención de un autor `no_identificado`
- **THEN** el LogRecord no incluye el atributo `social.identity.customer_pseudonym`

### Requirement: Severidad derivada del impacto
La severidad del LogRecord SHALL derivarse del impacto: `bajo`→INFO, `medio`→INFO2, `alto`→WARN, `critico`→ERROR, para que los backends puedan alertar con reglas estándar.

#### Scenario: Mención crítica
- **WHEN** se exporta una mención con impacto `critico`
- **THEN** su severidad es ERROR

### Requirement: Eventos de actualización
Las correcciones manuales de clasificación y las resoluciones tardías de identidad SHALL emitirse como LogRecords con event name `social.mention.updated` y `social.author.identity.resolved`, respectivamente, que referencien el `social.mention.id` o el `social.author.pseudonym_id` afectado.

#### Scenario: Corrección manual
- **WHEN** un analista corrige una dimensión
- **THEN** se emite `social.mention.updated` con el mismo `social.mention.id` y los valores corregidos

### Requirement: Entrega al menos una vez
La exportación SHALL ser al menos una vez: si el Collector no está disponible, el sistema MUST conservar los registros pendientes de forma durable y reenviarlos al recuperarse la conexión, sin perder ninguno.

#### Scenario: Collector caído
- **WHEN** el endpoint OTLP no responde durante una exportación
- **THEN** los registros quedan pendientes y se envían cuando el Collector vuelve a estar disponible

#### Scenario: Reinicio con registros pendientes
- **WHEN** la plataforma se reinicia con registros aún no confirmados por el Collector
- **THEN** los reenvía tras el arranque

### Requirement: Recurso OTel identificado
Toda la telemetría SHALL incluir los atributos de recurso `service.name=social-listener`, `service.version` y `deployment.environment.name`, y MUST enviarse por OTLP (gRPC o HTTP) con TLS cuando el endpoint lo requiera.

#### Scenario: Configuración del endpoint
- **WHEN** se definen las variables estándar `OTEL_EXPORTER_OTLP_*`
- **THEN** la plataforma exporta al endpoint, protocolo y cabeceras indicados

### Requirement: Métricas agregadas sin alta cardinalidad
El sistema SHALL emitir métricas de menciones procesadas por canal, tipo, sentimiento, categoría e impacto; de resultados de identidad por estado, y de latencia y errores por etapa. Los atributos de las métricas MUST NOT incluir ids de mención, autor, cliente ni texto.

#### Scenario: Conteo por tipo
- **WHEN** se procesan menciones
- **THEN** el contador `social.mentions.processed` aumenta con los atributos canal, tipo, sentimiento, categoría e impacto

#### Scenario: Atributo prohibido
- **WHEN** se registra una métrica
- **THEN** ninguno de sus atributos contiene identificadores de mención, autor o cliente

### Requirement: Trazas del pipeline
El sistema SHALL generar un span por etapa del pipeline (ingesta, normalización, protección, clasificación, individualización, identidad, exportación) para cada mención o lote, y el LogRecord exportado MUST llevar el trace_id y el span_id de su procesamiento.

#### Scenario: Correlación log–traza
- **WHEN** un operador encuentra un LogRecord `social.mention.enriched`
- **THEN** puede navegar a la traza de su procesamiento con el trace_id del registro
