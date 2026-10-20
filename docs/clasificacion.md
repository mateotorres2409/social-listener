# Clasificación de menciones

Cada mención se clasifica en seis dimensiones. Cada valor guarda su **origen** (`regla`,
`llm`, `respaldo` o `manual`), la **versión del clasificador**, una **confianza** entre 0 y 1
y, si lo produjo un modelo de lenguaje, el **proveedor** y el **modelo**.

## Dimensiones

| Dimensión | Cómo se obtiene | Valores |
|---|---|---|
| `canal` | Regla: red social y formato de la mención | `x`, `facebook`, `instagram`, `tiktok`, `youtube`, `reddit`, `resenas`, `mock`; formato `publicacion`, `comentario`, `respuesta`, `resena` |
| `tipo` | Modelo de lenguaje; respaldo por léxicos | ver catálogo abajo (alineado a PQRS) |
| `sentimiento` | Modelo de lenguaje; respaldo por léxicos | `positivo`, `neutral`, `negativo`, `mixto` + puntaje entre -1 y 1 |
| `categoria` | Modelo de lenguaje sobre la taxonomía activa; respaldo por léxicos | taxonomía del proyecto (por defecto, la financiera de abajo) |
| `tematica` | Modelo de lenguaje; se normaliza a slug y reutiliza las temáticas existentes | etiqueta corta, p. ej. `caida-app`, `cobro-cuota-manejo` |
| `impacto` | Regla (`ImpactPolicy`) con umbrales del proyecto | `bajo`, `medio`, `alto`, `critico` |

### Tipo

| Valor | Cuándo |
|---|---|
| `peticion` | Solicita a la entidad una acción o un trámite concreto |
| `queja` | Expresa inconformidad con la atención o el servicio |
| `reclamo` | Exige una corrección o compensación por un cobro o un error |
| `sugerencia` | Propone una mejora del servicio |
| `felicitacion` | Reconoce o agradece un buen servicio |
| `consulta` | Pregunta por información, requisitos o funcionamiento |
| `alerta_fraude` | Reporta fraude, suplantación, phishing o robo |
| `riesgo_reputacional` | Amenaza con denunciar o escalar a medios o reguladores |
| `informativa` | Menciona a la entidad sin pedir ni reclamar nada |
| `irrelevante` | No trata sobre la entidad o es spam |

### Taxonomía financiera por defecto (versión 1)

`tarjetas` (crédito, débito) · `cuentas` (ahorros, corriente) · `creditos` (consumo, vivienda,
vehículo) · `inversiones` (CDT, fondos) · `seguros` · `canales_digitales` (app, web) ·
`oficinas_cajeros` · `atencion_cliente` · `fraude_seguridad` · `tarifas_costos` ·
`reputacion_corporativa`.

Cada proyecto puede publicar su propia taxonomía con `PUT /v1/projects/{id}/taxonomy`. Cada
publicación crea una versión nueva; las menciones ya clasificadas conservan la versión con que
se clasificaron.

### Impacto

1. El **alcance** fija el nivel base: `alto` si el autor supera `influencer_followers` (10 000)
   o la mención `viral_interactions` (500); `medio` desde `medium_followers` (1 000) o
   `medium_interactions` (50); si no, `bajo`.
2. Lo **negativo** sube a `medio` si el alcance es bajo. Una queja, reclamo o petición negativa
   de alto alcance y viral es `critico`.
3. `alerta_fraude`, `riesgo_reputacional` y lo negativo en categorías de riesgo (fraude,
   reputación) tienen piso `alto` (`critico` si además el alcance es alto).
4. Lo positivo o neutral sin riesgo no supera `medio`.

### Revisión humana

Una mención queda marcada para revisión si alguna dimensión tiene confianza menor que
`review_confidence_threshold` del proyecto (0.6 por defecto) o si el modelo propuso un valor
fuera del catálogo. Un analista corrige con
`POST /v1/mentions/{id}/classification/corrections`: el valor queda con origen `manual` y
confianza 1, y se emite `social.mention.updated`.

## Modelos de lenguaje: varios proveedores

La plataforma no depende de un solo modelo. Cada proyecto tiene una **cadena ordenada** de
modelos. Si uno falla, no responde a tiempo o devuelve algo inválido, se usa el siguiente; si
todos fallan, se clasifica con las reglas de respaldo (origen `respaldo`). Todos los
proveedores reciben el mismo prompt y el mismo esquema de salida, y solo el texto enmascarado.

| Proveedor | Para qué | Campos requeridos |
|---|---|---|
| `anthropic` | API de Claude | `credential_ref` |
| `anthropic_bedrock` | Claude en Amazon Bedrock (ids con prefijo `anthropic.`) | `options.region` |
| `anthropic_vertex` | Claude en Google Vertex AI | `options.project_id`, `options.region` |
| `ollama` | Modelos locales en la infraestructura de la entidad | `base_url` (opcional) |
| `openai_compatible` | OpenAI, Azure OpenAI, vLLM, LM Studio, Gemini compatible, … | `base_url`; `credential_ref` si la API lo exige; `capabilities: ["json_object"]` si el servidor no admite `json_schema` |

Ejemplo:

```
PUT /v1/projects/{id}/model-chain
{"chain": [
  {"provider": "ollama", "model": "qwen3:4b", "base_url": "http://ollama:11434", "timeout_s": 30},
  {"provider": "anthropic", "model": "claude-haiku-5-5", "credential_ref": "anthropic-api-key", "timeout_s": 20}
]}
```

`credential_ref` es una **referencia** al gestor de secretos (en local, la variable
`SL_SECRET_ANTHROPIC_API_KEY`); la clave nunca se guarda en la configuración. Una cadena vacía
desactiva los modelos de lenguaje del proyecto.

### Cómo aprobar un proveedor o un modelo nuevo

1. Confirmar con seguridad de la información dónde corre el modelo y qué datos recibe (solo el
   texto enmascarado y metadatos no identificables).
2. Correr la evaluación contra el conjunto etiquetado:

   ```
   uv run python -m social_listener.bootstrap.eval_classification \
       --provider ollama --model qwen3:4b --base-url http://localhost:11434
   ```

   La línea base sin modelo es `--provider reglas`.
3. Comparar la fila nueva de la tabla de resultados con los modelos ya aprobados. Prestar
   atención a la tasa de salidas inválidas y a la latencia p95 frente al timeout configurado.
4. Si la calidad es suficiente, agregarlo a la cadena del proyecto con
   `PUT /v1/projects/{id}/model-chain`.
5. Para un proveedor que no esté en la tabla: escribir un adaptador de `LLMClassifier` en
   `src/social_listener/adapters/outbound/llm/`, probarlo con la batería
   `LLMClassifierContract` y registrarlo en `src/social_listener/bootstrap/models.py`.

## Resultados de la evaluación

El conjunto de evaluación (`eval/menciones_financieras.jsonl`, 101 menciones) es **sintético**:
menciones redactadas para cubrir todos los tipos y categorías, sin datos de personas reales.
Antes de aprobar un modelo para producción hay que ampliarlo con menciones reales
**anonimizadas** de la entidad, etiquetadas por sus analistas.

<!-- resultados:inicio -->
| Proveedor | Modelo | N | Tipo | Sentimiento | Categoría | Salidas inválidas | No disponibles | Latencia p50 | Latencia p95 | Fecha |
|---|---|---|---|---|---|---|---|---|---|---|
| reglas | léxicos | 101 | 67% | 58% | 70% | 0% | 0 | 0.00 s | 0.00 s | 2026-10-20 |
<!-- resultados:fin -->
