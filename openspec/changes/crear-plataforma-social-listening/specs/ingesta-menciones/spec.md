# Spec Delta

## Purpose

Capturar menciones públicas desde múltiples canales sociales según tópicos de escucha configurados y convertirlas en un modelo canónico de Mención, único y sin duplicados, listo para el resto del pipeline.

## ADDED Requirements

### Requirement: Gestión de tópicos de escucha
El sistema SHALL permitir crear, consultar, actualizar, pausar y eliminar tópicos de escucha por proyecto mediante la API de administración. Cada tópico define términos, cuentas, hashtags, canales, idiomas y su estado (activo/pausado).

#### Scenario: Crear un tópico válido
- **WHEN** un administrador envía un tópico con al menos un término o cuenta y al menos un canal
- **THEN** el sistema lo persiste en estado activo y responde con su identificador

#### Scenario: Rechazar un tópico sin criterios
- **WHEN** un administrador envía un tópico sin términos, cuentas ni hashtags
- **THEN** el sistema responde con un error de validación y no persiste el tópico

#### Scenario: Pausar un tópico
- **WHEN** un tópico pasa a estado pausado
- **THEN** el sistema deja de capturar menciones para ese tópico a partir del siguiente ciclo de ingesta

### Requirement: Contrato común de conectores
Toda fuente SHALL integrarse mediante un conector que cumpla un contrato común: recibe un tópico de escucha y un checkpoint, y entrega menciones crudas junto con un nuevo checkpoint. Esta entrega MUST incluir un conector simulado y un conector de reproducción de fixtures.

#### Scenario: Conector simulado disponible
- **WHEN** se configura un tópico con el canal `mock`
- **THEN** el sistema genera menciones sintéticas que recorren todo el pipeline

#### Scenario: Reproducción de fixtures
- **WHEN** se configura un tópico con el canal `fixture` apuntando a un archivo de menciones grabadas
- **THEN** el sistema ingiere exactamente las menciones de ese archivo en el orden definido

### Requirement: Captura incremental con checkpoints
El sistema SHALL persistir un checkpoint por combinación de tópico y conector después de cada lote ingerido con éxito, y MUST reanudar desde ese checkpoint tras un reinicio o una falla.

#### Scenario: Reanudar tras reinicio
- **WHEN** el proceso de ingesta se reinicia después de haber ingerido un lote
- **THEN** la siguiente captura comienza desde el último checkpoint persistido y no reprocesa el lote anterior

### Requirement: Tolerancia a límites y fallas de la fuente
Ante límites de tasa o errores transitorios de una fuente, el sistema SHALL reintentar con backoff exponencial y jitter hasta un máximo configurable, sin avanzar el checkpoint de los lotes no confirmados.

#### Scenario: Límite de tasa de la fuente
- **WHEN** una fuente responde con un límite de tasa
- **THEN** el sistema espera según el backoff (o el tiempo que indique la fuente) y reintenta sin perder ni duplicar menciones

#### Scenario: Reintentos agotados
- **WHEN** un lote supera el número máximo de reintentos
- **THEN** el sistema registra la falla con su causa, emite telemetría de error y conserva el checkpoint anterior

### Requirement: Normalización al modelo canónico
Toda mención cruda SHALL convertirse al modelo canónico de Mención, que incluye al menos: canal, id externo, URL, texto, idioma, fecha de publicación, autor (id externo, username, nombre visible, seguidores, verificado), métricas de interacción, id del hilo/padre y tópico que la capturó.

#### Scenario: Mención completa
- **WHEN** un conector entrega una mención con todos los campos
- **THEN** el sistema persiste una Mención canónica con esos campos mapeados

#### Scenario: Campos opcionales ausentes
- **WHEN** un conector entrega una mención sin seguidores del autor ni métricas de interacción
- **THEN** el sistema persiste la Mención con esos campos vacíos y la procesa igual

#### Scenario: Mención inválida
- **WHEN** un conector entrega una mención sin id externo o sin texto
- **THEN** el sistema la rechaza, la envía a la cola de errores con el motivo y continúa con el resto del lote

### Requirement: Deduplicación idempotente
El sistema SHALL garantizar que exista una sola Mención por par (canal, id externo), aunque la capture más de un tópico o se reingiera.

#### Scenario: Misma mención en dos tópicos
- **WHEN** dos tópicos capturan la misma publicación
- **THEN** existe una sola Mención asociada a ambos tópicos y se procesa una sola vez

#### Scenario: Reingesta de un lote
- **WHEN** se reingiere un lote ya procesado
- **THEN** no se crean Menciones nuevas ni se reexportan las existentes

### Requirement: Cola de errores y reprocesamiento
Toda mención que falle de forma no transitoria en cualquier etapa SHALL quedar en una cola de errores con la etapa, el motivo y el número de intentos, y MUST poder reprocesarse desde la API de administración.

#### Scenario: Reprocesar una mención fallida
- **WHEN** un administrador solicita reprocesar una mención de la cola de errores
- **THEN** la mención reingresa al pipeline desde la etapa en la que falló
