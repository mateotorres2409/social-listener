# Conectores de fuentes

Un conector es un **adaptador de salida** que implementa el puerto `SourceConnector`
(`src/social_listener/application/ports/outbound/services.py`). La ingesta
(`IngestMentions`) no sabe de qué red social viene cada mención: solo habla con este puerto.

## Contrato

```python
class SourceConnector(Protocol):
    @property
    def name(self) -> str: ...

    async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch: ...
```

- `name` es el identificador que se usa en `channels` de un tópico de escucha
  (por ejemplo `"mock"`, `"fixture"` o, en el futuro, `"x"`).
- `fetch` recibe el tópico y el último checkpoint confirmado (`None` la primera vez) y
  devuelve un `ConnectorBatch`:
  - `mentions`: lista de `RawMention` (todos sus campos son opcionales; la normalización
    decide si la mención es válida y envía las inválidas a la cola de errores).
  - `next_checkpoint`: valor opaco desde el que se pide el siguiente lote. La ingesta lo
    persiste **solo** cuando el lote quedó guardado, así que tras un reinicio nunca se
    pierde ni se duplica un lote.
  - `has_more`: `True` si hay más lotes disponibles en este ciclo.
- Errores:
  - `RateLimitedError(retry_after_s)` y `TransientSourceError`: la ingesta reintenta con
    backoff exponencial y jitter (respetando `retry_after_s`) sin avanzar el checkpoint.
  - `SourceError`: falla definitiva (p. ej. credenciales rechazadas). No se reintenta en
    ese ciclo. El mensaje nunca debe incluir credenciales.

## Cómo agregar un conector

1. Crear `src/social_listener/adapters/outbound/connectors/<fuente>.py` con una clase que
   cumpla el contrato. Este es el esqueleto del conector `mock`, que sirve de referencia:

   ```python
   class MockConnector:
       name = "mock"

       async def fetch(self, query: ListeningQuery, checkpoint: str | None) -> ConnectorBatch:
           index = int(checkpoint or 0)
           if index >= self._batches:
               return ConnectorBatch(mentions=[], next_checkpoint=checkpoint, has_more=False)
   ```

2. Mapear la respuesta de la API de la fuente a `RawMention` (canal, id externo, URL,
   texto, idioma, fecha con zona horaria, autor, métricas, id del padre). Lo que no tenga
   un campo propio va en `source_payload`.
3. Traducir los errores de la API a `RateLimitedError`, `TransientSourceError` o
   `SourceError`. Las credenciales se leen del gestor de secretos, nunca del código.
4. Registrarlo en `src/social_listener/bootstrap/connectors.py`.
5. Probarlo con la batería `SourceConnectorContract` (`tests/contracts/services.py`) contra
   respuestas grabadas, como hacen `tests/unit/test_connectors.py` con `mock` y `fixture`.

## Conectores incluidos

| Nombre | Uso | Configuración |
|---|---|---|
| `mock` | Menciones sintéticas deterministas para desarrollo | `SL_ENABLE_MOCK_CONNECTOR` (por defecto `true`) |
| `fixture` | Reproduce menciones grabadas en JSON Lines, en orden | `SL_FIXTURE_PATH=/ruta/menciones.jsonl` |

Formato de una línea del fixture:

```json
{"channel": "instagram", "external_id": "f-2", "url": "https://instagram.com/p/f-2", "text": "…", "language": "es", "published_at": "2026-10-01T10:05:00+00:00", "author_external_id": "a-2", "author_username": "luis", "format": "comentario", "author_followers": 300, "likes": 4}
```
