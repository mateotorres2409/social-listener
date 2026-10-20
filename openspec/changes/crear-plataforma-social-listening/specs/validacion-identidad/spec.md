# Spec Delta

## Purpose

Determinar, mediante el microservicio de identidad de la entidad, si el autor de una mención corresponde a un cliente, obteniendo solo una referencia seudonimizada y sin bloquear el pipeline cuando el servicio falle.

## ADDED Requirements

### Requirement: Consulta al microservicio de identidad
Para cada autor sin un resultado de identidad vigente, el sistema SHALL consultar el microservicio de identidad enviando únicamente el canal, el id externo del autor y su username vigente, y MUST autenticarse con credenciales de servicio sobre TLS.

#### Scenario: Autor nuevo
- **WHEN** se individualiza un autor que nunca se ha validado
- **THEN** el sistema consulta el microservicio con canal, id externo y username del autor

#### Scenario: Datos enviados
- **WHEN** se realiza una consulta de identidad
- **THEN** la solicitud no incluye el texto de la mención ni otros datos del perfil

### Requirement: Estados del resultado de identidad
Todo resultado de identidad SHALL tener uno de estos estados: `identificado`, `no_identificado`, `ambiguo`, `pendiente` o `error`. Solo el estado `identificado` MUST incluir una referencia de cliente opaca emitida por el microservicio, junto con un nivel de confianza.

#### Scenario: Cliente identificado
- **WHEN** el microservicio confirma que el username corresponde a un cliente
- **THEN** el autor queda con estado `identificado`, la referencia opaca y la confianza recibidas

#### Scenario: Coincidencia ambigua
- **WHEN** el microservicio indica que el username coincide con más de un cliente
- **THEN** el autor queda con estado `ambiguo` y sin referencia de cliente

#### Scenario: No es cliente
- **WHEN** el microservicio no encuentra coincidencias
- **THEN** el autor queda con estado `no_identificado`

### Requirement: Minimización de datos de identidad
El sistema SHALL almacenar y procesar únicamente el estado, la referencia opaca, la confianza y la fecha de validación. MUST NOT solicitar, almacenar ni exportar nombres, documentos, productos ni otros datos del cliente.

#### Scenario: Respuesta con datos extra
- **WHEN** el microservicio devuelve campos adicionales a los del contrato
- **THEN** el sistema los descarta sin persistirlos ni registrarlos

### Requirement: Caché y revalidación
El sistema SHALL reutilizar el resultado de identidad de un autor durante un tiempo de vida configurable por estado, y MUST revalidarlo cuando ese tiempo venza o cuando cambie el username del autor.

#### Scenario: Autor ya validado
- **WHEN** llega una nueva mención de un autor con un resultado vigente
- **THEN** el sistema no consulta el microservicio y usa el resultado almacenado

#### Scenario: Cambio de username
- **WHEN** un autor identificado cambia de username
- **THEN** el sistema invalida su resultado y lo vuelve a consultar

### Requirement: Tolerancia a fallas sin bloqueo
Si el microservicio no responde dentro del tiempo límite configurable o falla, el sistema SHALL continuar el pipeline con estado `pendiente`, reintentar en segundo plano con backoff y suspender temporalmente las consultas cuando la tasa de fallas supere un umbral.

#### Scenario: Microservicio lento
- **WHEN** el microservicio no responde dentro del tiempo límite
- **THEN** la mención se exporta con estado de identidad `pendiente` y la validación se reintenta en segundo plano

#### Scenario: Microservicio caído
- **WHEN** las fallas consecutivas superan el umbral
- **THEN** el sistema deja de consultarlo durante el periodo de enfriamiento, marca las nuevas menciones como `pendiente` y emite telemetría de la falla

#### Scenario: Reintentos agotados
- **WHEN** un autor agota los reintentos de validación
- **THEN** su estado pasa a `error` y queda disponible para revalidación manual desde la API de administración

### Requirement: Resolución tardía de identidad
Cuando un autor con estado `pendiente` obtiene un resultado definitivo, el sistema SHALL emitir un evento de actualización de identidad por OpenTelemetry que permita correlacionarlo con las menciones ya exportadas de ese autor.

#### Scenario: Identidad resuelta después de exportar
- **WHEN** un reintento en segundo plano identifica a un autor cuyas menciones se exportaron como `pendiente`
- **THEN** se emite un evento de actualización con el seudónimo del autor, el nuevo estado y la referencia seudonimizada del cliente

### Requirement: Contrato del microservicio de identidad
La plataforma SHALL publicar el contrato que espera del microservicio de identidad (OpenAPI) e incluir un stub que lo implemente para desarrollo y pruebas.

#### Scenario: Ejecución local
- **WHEN** se levanta el entorno local
- **THEN** el stub responde según el contrato publicado con resultados configurables para cada estado
