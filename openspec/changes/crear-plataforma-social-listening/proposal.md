# Proposal

## Why

Las entidades financieras reciben a diario en redes sociales quejas, consultas, alertas de fraude y riesgos reputacionales que hoy se revisan a mano, tarde y sin saber si quien escribe es cliente. Necesitan una plataforma que escuche esas conversaciones, las clasifique con criterios de negocio y regulatorios, identifique de forma segura a los autores que son clientes y entregue cada comentario enriquecido a sus herramientas de observabilidad y análisis por OpenTelemetry, el estándar que ya operan.

## What Changes

- Nueva plataforma (greenfield) en Python + FastAPI con un pipeline por etapas: ingesta → normalización/deduplicación → protección de datos → clasificación → individualización → validación de identidad → exportación OTel.
- La plataforma se construye con Clean Architecture y puertos y adaptadores (hexagonal). Las reglas de negocio y los casos de uso quedan aislados de los frameworks y de las integraciones (redes sociales, LLM, identidad, base de datos, OpenTelemetry), así que cambiar un proveedor no toca el núcleo. La regla de dependencias se verifica automáticamente.
- Ingesta mediante conectores intercambiables con una interfaz común, checkpoints y deduplicación. Esta entrega incluye un conector simulado y un conector de reproducción de fixtures; los conectores reales de cada red quedan para cambios posteriores.
- Clasificación de cada mención en seis dimensiones: **canal**, **impacto**, **tipo** (alineado a PQRS), **sentimiento**, **categorización** (taxonomía de productos y servicios financieros, configurable) y **temática**. El motor es híbrido: reglas deterministas para canal e impacto, y modelos de lenguaje con salida estructurada y reglas de respaldo para las demás dimensiones.
- Modelos de lenguaje multi-proveedor: no se depende de un solo modelo. Cada proyecto configura una cadena ordenada de proveedores y modelos con respaldo entre ellos. Se soportan Anthropic Claude (API directa, Bedrock o Vertex), Ollama (modelos locales en la infraestructura de la entidad) y cualquier API compatible con OpenAI (OpenAI, Azure OpenAI, vLLM, LM Studio, entre otros).
- Individualización por autor: perfil por canal, historial, sentimiento acumulado e hilos de conversación.
- Validación de identidad: integración por API con un microservicio externo de identidad que indica si un username corresponde a un cliente y devuelve solo una referencia seudonimizada. Esta propuesta define el contrato esperado y un stub para desarrollo.
- Protección de datos: enmascaramiento de datos sensibles en el texto (números de tarjeta y de cuenta, documentos de identidad, teléfonos, correos) antes de enviarlo a cualquier modelo de lenguaje o exportarlo; seudonimización de los identificadores de autor; retención configurable.
- Exportación por OpenTelemetry (OTLP): un LogRecord por mención enriquecida con atributos `social.*` versionados, métricas agregadas sin alta cardinalidad y trazas por etapa del pipeline.
- API de administración (FastAPI) para gestionar tópicos de escucha y taxonomía y consultar menciones y autores.

## Capabilities

### New Capabilities
- `ingesta-menciones`: tópicos de escucha, contrato de conectores, captura con checkpoints, normalización al modelo canónico de Mención y deduplicación.
- `proteccion-datos`: enmascaramiento de datos sensibles en el texto, seudonimización de identificadores y retención o supresión de datos personales.
- `clasificacion-menciones`: clasificación en las seis dimensiones, taxonomía configurable, trazabilidad del clasificador (origen, proveedor, modelo, versión, confianza), selección de proveedores y modelos por proyecto, y degradación ante fallas de los modelos.
- `individualizacion-autores`: resolución de autores por canal, perfil e historial por autor y reconstrucción de hilos.
- `validacion-identidad`: consulta al microservicio de identidad, estados del match, caché, tolerancia a fallas y resolución tardía.
- `exportacion-otel`: emisión OTLP de menciones enriquecidas como logs, métricas agregadas y trazas del pipeline, con esquema de atributos versionado y entrega al menos una vez.

### Modified Capabilities
<!-- Ninguna: no existen specs previas en el proyecto. -->

## Impact

- **Código:** repositorio nuevo. El paquete `social_listener` se organiza en las capas `domain`, `application` (casos de uso y puertos), `adapters` (entrada y salida) y `bootstrap` (composición). También se crean las migraciones de base de datos, la API de administración, los workers del pipeline y las pruebas.
- **Dependencias:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy y Alembic, PostgreSQL, SDK de OpenTelemetry para Python (exportador OTLP), SDK de Anthropic (Claude), SDK de OpenAI (para APIs compatibles), HTTPX (también para la API nativa de Ollama) e import-linter para verificar la regla de dependencias entre capas.
- **Sistemas externos:**
  - Microservicio de identidad del cliente (contrato definido aquí; la implementación real la provee la entidad).
  - OpenTelemetry Collector y los backends que la entidad elija.
  - Proveedores de modelos de lenguaje según cada proyecto: API de Claude (directa o en una región aprobada vía Bedrock o Vertex), instancias de Ollama de la entidad o endpoints compatibles con OpenAI.
- **Cumplimiento:** ingresan al sistema datos personales de redes sociales y se cruzan con la base de clientes. Aplican la Ley 1581 de 2012 (Habeas Data), el régimen de PQRS y la protección al consumidor financiero, y las políticas internas de seguridad de la información de la entidad.
- **Infraestructura local:** Docker Compose con PostgreSQL, OTel Collector, un backend de visualización para verificación y el stub de identidad.
