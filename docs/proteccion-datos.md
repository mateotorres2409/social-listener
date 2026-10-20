# Protección de datos

La plataforma recibe datos personales de redes sociales y los cruza con la base de
clientes de una entidad financiera. Este documento describe qué se protege, cómo y dónde.
Marco de referencia: Ley 1581 de 2012 (Habeas Data) y principios equivalentes de GDPR
(minimización, retención limitada y derecho de supresión).

## Enmascaramiento del texto

La etapa `proteccion` (caso de uso `ProtectMention`) es la primera del pipeline. Detecta
los datos sensibles del texto original y guarda una versión enmascarada. **Todo lo que viene
después (modelos de lenguaje, exportación OTel, API) solo ve el texto enmascarado.** El texto
original queda en la columna `mentions.raw_text`, que solo leen la protección y la retención
(una prueba lo verifica sobre el código de los casos de uso).

| Dato | Marcador | Cómo se detecta |
|---|---|---|
| Tarjeta | `[TARJETA]` | 13 a 19 dígitos, con o sin espacios o guiones, que superan la validación de Luhn |
| Cuenta | `[CUENTA]` | Número precedido de "cuenta", "cta", "número de cuenta" (ahorros, corriente); y cualquier secuencia de 9 o más dígitos que no encaje en otra regla |
| Documento | `[DOCUMENTO]` | Número precedido de "cédula", "CC", "documento", "TI", "pasaporte" o "NIT"; NIT con formato `900.123.456-7` |
| Teléfono | `[TELEFONO]` | Celular colombiano `3xx xxx xxxx` y fijo `60x xxx xxxx`, con o sin `+57` |
| Correo | `[EMAIL]` | Formato `usuario@dominio.tld` |

Ejemplo: `me cobraron doble en la tarjeta 4111 1111 1111 1111` →
`me cobraron doble en la tarjeta [TARJETA]`.

Un número de 16 dígitos que no supera Luhn **no** se marca como tarjeta, pero sí como
`[CUENTA]`: ante la duda se prefiere enmascarar. La mención queda marcada con
**exposición de datos sensibles** y con los tipos detectados (nunca los valores), para que la
entidad pueda gestionarla.

El detector es un adaptador (`RuleBasedDetector`) del puerto `SensitiveDataDetector`. Para
agregar otro (por ejemplo Microsoft Presidio) basta con implementar el mismo puerto y
cablearlo en `bootstrap/container.py`.

## Seudonimización

Los identificadores que salen de la plataforma se exportan como seudónimos estables con
HMAC-SHA256 (`HmacPseudonymizer`):

- `author_pseudonym_id` = HMAC(clave, `"autor"` + canal + id externo del autor).
- `customer_pseudonym` = HMAC(clave, `"cliente"` + referencia opaca del microservicio de identidad).

Formato: `<key_id>:<32 caracteres hexadecimales>`. El mismo autor produce siempre el mismo
seudónimo, así que los consumidores pueden correlacionar sus menciones. Sin la clave, el
seudónimo no se puede revertir.

### Rotación de la clave

1. Generar una clave nueva de al menos 16 bytes en el gestor de secretos.
2. Cambiar `SL_PSEUDONYMIZATION_KEY` y `SL_PSEUDONYMIZATION_KEY_ID` (por ejemplo, de `k1` a `k2`).
3. Reiniciar los workers. A partir de ese momento los seudónimos empiezan por `k2:`; los
   consumidores deben tratar el cambio de prefijo como una nueva serie de identificadores.

## Retención

Cada proyecto define `retention_days` (365 por defecto). El proceso
`social-listener worker --stage retention` corre cada `SL_RETENTION_INTERVAL_S` (un día por
defecto) y, para lo que superó el periodo:

- elimina el texto original de las menciones y anonimiza los datos del autor copiados en ellas
  (username, nombre, seguidores);
- elimina los perfiles de autor sin actividad en el periodo, junto con su resultado de identidad;
- conserva la mención enmascarada y su clasificación, que ya no identifican a nadie;
- registra la operación en `audit_log` solo con conteos.

## Supresión por solicitud del titular

```
POST /v1/projects/{project_id}/authors/suppress
{"channel": "x", "external_id": "123", "requested_by": "oficial-de-privacidad"}
```

Elimina el perfil del autor, sus textos originales y su resultado de identidad, y anonimiza
sus menciones. Queda un registro en `audit_log` cuyo sujeto es el seudónimo del autor, sin
datos personales. Las menciones futuras de esa persona se procesan como si fuera un autor nuevo.

## Secretos

Las credenciales (base de datos, token de la API, clave de seudonimización, proveedores de
modelos, microservicio de identidad) se leen del entorno o del gestor de secretos y nunca se
escriben en el código. Un filtro de logs (`RedactingFilter`) reemplaza por `[REDACTADO]`:

- los valores de los secretos configurados;
- patrones de credenciales: `Bearer …`, `api_key=…`, `token=…`, `password=…` y contraseñas en URLs.

La telemetría exportada nunca incluye secretos ni el mensaje de las excepciones, solo su tipo.
