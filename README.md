# Social Listener

Plataforma de escucha social para entidades financieras. Captura menciones públicas en redes
sociales, protege los datos sensibles, las clasifica en seis dimensiones, individualiza a cada
autor, valida si es cliente contra el microservicio de identidad de la entidad y exporta cada
comentario enriquecido por **OpenTelemetry** al Collector que la entidad ya opera.

## Qué hace

| Capacidad | Resumen |
|---|---|
| Ingesta | Tópicos de escucha, conectores intercambiables (`mock`, `fixture`), checkpoints, deduplicación por `(canal, id externo)` y cola de errores reprocesable |
| Protección de datos | Enmascara tarjetas (Luhn), cuentas, documentos, teléfonos y correos; seudonimiza con HMAC; retención y supresión por titular. Ver [docs/proteccion-datos.md](docs/proteccion-datos.md) |
| Clasificación | Canal, impacto, tipo (PQRS), sentimiento, categoría y temática. Híbrida: reglas + modelos de lenguaje de **varios proveedores** (Claude, Ollama, APIs compatibles con OpenAI) con cadena de respaldo. Ver [docs/clasificacion.md](docs/clasificacion.md) |
| Individualización | Perfil por autor y canal, historial, sentimiento acumulado, autores recurrentes e hilos de conversación |
| Identidad | Consulta al microservicio de la entidad, con caché, circuit breaker y resolución tardía. Ver [docs/identidad.md](docs/identidad.md) |
| Exportación | Un LogRecord OTLP por mención con atributos `social.*` versionados, entrega al menos una vez, métricas y trazas. Ver [docs/esquema-otel.md](docs/esquema-otel.md) |

Las especificaciones están en [`openspec/`](openspec/) (OpenSpec).

## Arquitectura

Clean Architecture con puertos y adaptadores (hexagonal). El núcleo no depende de ningún
framework ni de la infraestructura; la regla se verifica en CI con `import-linter`.

```
           adaptadores de entrada                       adaptadores de salida
     ┌───────────────────────────┐              ┌────────────────────────────────────┐
     │ API FastAPI  (/v1/...)    │              │ PostgreSQL (repos, UoW, cola, outbox)│
     │ Workers por etapa (CLI)   │              │ Conectores: mock, fixture, …        │
     │ Despachador del outbox    │              │ Modelos: Anthropic, Ollama, OpenAI- │
     └─────────────┬─────────────┘              │   compatible, cadena, nulo          │
                   │                            │ Microservicio de identidad (HTTPX)  │
                   ▼                            │ Detector de datos sensibles, HMAC   │
     ┌───────────────────────────────────┐      │ OpenTelemetry (OTLP)                │
     │ application                       │      └──────────────────┬─────────────────┘
     │  casos de uso + puertos ◄─────────┼─────────────────────────┘ implementan
     │ ┌───────────────────────────────┐ │                            los puertos
     │ │ domain                        │ │
     │ │  entidades y políticas puras  │ │
     │ └───────────────────────────────┘ │
     └───────────────────────────────────┘
       bootstrap/ arma el grafo de dependencias según el entorno
```

Pipeline (cada etapa es un caso de uso y un worker; el resultado, la siguiente etapa y el
cierre del trabajo se confirman en la misma transacción):

```
ingesta → protección → clasificación → individualización → identidad → exportación → outbox → OTLP
```

## Arranque local

Requisitos: Docker y [uv](https://docs.astral.sh/uv/).

```bash
# 1. Entorno completo: PostgreSQL, API, workers, stub de identidad, Ollama,
#    OpenTelemetry Collector y Grafana (otel-lgtm)
docker compose -f deploy/docker-compose.yml up -d --build

# 2. Esperar a que Ollama descargue el modelo (servicio ollama-pull)
docker compose -f deploy/docker-compose.yml logs -f ollama-pull

# 3. Prueba de punta a punta (crea un proyecto, ingiere un fixture y verifica lo
#    que recibió el Collector)
SL_E2E=1 uv run pytest tests/e2e -v
```

- API: http://localhost:8000/docs (token `Bearer token-local`)
- Grafana con logs, métricas y trazas: http://localhost:3000
- Copia de los logs que recibe el Collector: `deploy/data/logs.jsonl`

Desarrollo sin Docker Compose:

```bash
uv sync
uv run pytest            # pruebas unitarias y de integración (testcontainers necesita Docker)
uv run ruff check . && uv run mypy && uv run lint-imports
```

## Comandos

| Comando | Qué hace |
|---|---|
| `social-listener api` | API de administración |
| `social-listener ingest [--once]` | Ciclos de ingesta de todos los tópicos activos |
| `social-listener worker --stage <etapa>` | Worker de una etapa: `proteccion`, `clasificacion`, `individualizacion`, `identidad`, `identidad_reintento`, `exportacion` o `retention` |
| `social-listener dispatch` | Envía el outbox al Collector por OTLP |
| `alembic upgrade head` | Migraciones de base de datos |
| `python -m social_listener.bootstrap.eval_classification --provider <p> --model <m>` | Evalúa un proveedor o modelo de lenguaje |

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `SL_DATABASE_URL` | sí | `postgresql+asyncpg://…` |
| `SL_PSEUDONYMIZATION_KEY` | sí | Clave HMAC de seudonimización (≥ 16 bytes) |
| `SL_PSEUDONYMIZATION_KEY_ID` | sí | Identificador de la clave (para rotarla) |
| `SL_API_TOKEN` | sí | Token de servicio de la API de administración |
| `SL_ENVIRONMENT` | no | `local`, `test`, `staging` o `production` |
| `SL_IDENTITY_SERVICE_URL`, `SL_IDENTITY_TIMEOUT_S` | no | Microservicio de identidad |
| `SL_IDENTITY_TOKEN_URL`, `SL_IDENTITY_CLIENT_ID`, `SL_IDENTITY_CLIENT_SECRET` | no | OAuth2 client_credentials hacia identidad |
| `SL_IDENTITY_CLIENT_CERT`, `SL_IDENTITY_CLIENT_KEY`, `SL_IDENTITY_CA_FILE` | no | TLS mutuo hacia identidad |
| `SL_SECRET_<REF>` | no | Secretos referenciados por `credential_ref` (p. ej. claves de proveedores de modelos) |
| `SL_ENABLE_MOCK_CONNECTOR`, `SL_FIXTURE_PATH` | no | Conectores de desarrollo |
| `SL_INGEST_INTERVAL_S`, `SL_RETENTION_INTERVAL_S`, `SL_STAGE_MAX_ATTEMPTS` | no | Ritmo de los procesos |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_RESOURCE_ATTRIBUTES` | no | Destino OTLP (estándar de OpenTelemetry) |

## Documentación

- [docs/conectores.md](docs/conectores.md): cómo agregar una fuente.
- [docs/proteccion-datos.md](docs/proteccion-datos.md): enmascaramiento, seudonimización, retención y supresión.
- [docs/clasificacion.md](docs/clasificacion.md): dimensiones, proveedores de modelos y evaluación.
- [docs/identidad.md](docs/identidad.md): contrato y estados del microservicio de identidad.
- [docs/esquema-otel.md](docs/esquema-otel.md): eventos, atributos y versionado de la exportación.
