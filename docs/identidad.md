# Validación de identidad

La plataforma pregunta al microservicio de identidad de la entidad si el autor de una mención
es cliente. Solo envía **canal, id externo y username**, y solo guarda **estado, referencia
opaca, confianza y fecha**. Nunca pide ni guarda nombres, documentos ni productos.

El contrato completo está en
[`contracts/identity-service.openapi.yaml`](../contracts/identity-service.openapi.yaml). Para
desarrollo hay un stub en `stubs/identity/` que lo implementa.

## Contrato

Solicitud:

```json
{"channel": "x", "external_author_id": "123", "username": "juanp"}
```

Respuestas:

```json
{"status": "match", "customer_ref": "c-7f3a9e", "confidence": 0.94}
```

```json
{"status": "no_match", "customer_ref": null, "confidence": 0.0}
```

```json
{"status": "ambiguous", "customer_ref": null, "confidence": 0.5}
```

Los campos adicionales que devuelva el servicio se descartan sin leerse.

## Estados

| Estado | Significado | Se exporta |
|---|---|---|
| `identificado` | El username corresponde a un cliente | `social.identity.status=identificado` y `social.identity.customer_pseudonym` |
| `no_identificado` | No es cliente (también las cuentas oficiales de la entidad, que no se consultan) | solo el estado |
| `ambiguo` | Coincide con más de un cliente; no se guarda referencia | solo el estado |
| `pendiente` | El servicio no respondió; se reintenta en segundo plano | solo el estado |
| `error` | Se agotaron los reintentos; espera revalidación manual | solo el estado |

La referencia del cliente nunca sale de la plataforma en claro: se exporta seudonimizada con
HMAC (ver `docs/proteccion-datos.md`).

## Vigencia (TTL) y revalidación

| Estado | Se reutiliza durante |
|---|---|
| `identificado` | 30 días |
| `no_identificado` | 7 días |
| `ambiguo` | 7 días |
| `pendiente` | nunca: se reintenta |
| `error` | indefinidamente, hasta una revalidación manual |

Si el autor cambia de username, el resultado se invalida y se vuelve a consultar.

Revalidación manual:

```
POST /v1/authors/{author_id}/identity/revalidate   → 202 {"status": "pendiente"}
```

## Tolerancia a fallas

- **Timeout** configurable (`SL_IDENTITY_TIMEOUT_S`, 2 s por defecto). Si se vence, la mención
  sigue hacia la exportación con estado `pendiente` y se encola un reintento (etapa
  `identidad_reintento`) con backoff exponencial.
- **Circuit breaker**: tras 5 fallas consecutivas deja de llamar al servicio durante 30 s; las
  menciones nuevas quedan `pendiente` sin esperar el timeout.
- **Reintentos agotados** (`SL_STAGE_MAX_ATTEMPTS`): el autor pasa a `error`.
- **Resolución tardía**: cuando un autor `pendiente` obtiene un resultado definitivo, se emite
  `social.author.identity.resolved` con el seudónimo del autor, el estado nuevo y, si aplica,
  el seudónimo del cliente, para correlacionarlo con las menciones ya exportadas.

## Configuración y credenciales

| Variable | Uso |
|---|---|
| `SL_IDENTITY_SERVICE_URL` | URL base del servicio |
| `SL_IDENTITY_TIMEOUT_S` | Timeout por consulta, en segundos |
| `SL_IDENTITY_TOKEN_URL`, `SL_IDENTITY_CLIENT_ID`, `SL_IDENTITY_CLIENT_SECRET` | OAuth2 client_credentials (el token se cachea hasta poco antes de vencer) |
| `SL_IDENTITY_CLIENT_CERT`, `SL_IDENTITY_CLIENT_KEY`, `SL_IDENTITY_CA_FILE` | TLS mutuo, si el servicio lo exige |

Los secretos se leen del gestor de secretos o del entorno y se redactan de los logs.
