# Spec Delta

## Purpose

Proteger los datos personales y financieros que llegan en las menciones: enmascararlos antes de que salgan de la plataforma, seudonimizar los identificadores de autor y aplicar la retención y la supresión que exige la regulación de protección de datos.

## ADDED Requirements

### Requirement: Enmascaramiento de datos sensibles en el texto
Antes de enviar el texto a un clasificador externo o exportarlo, el sistema SHALL detectar y reemplazar por marcadores tipados (p. ej. `[TARJETA]`, `[CUENTA]`, `[DOCUMENTO]`, `[TELEFONO]`, `[EMAIL]`) los datos sensibles. El texto original MUST conservarse solo en el almacenamiento interno, con acceso restringido.

#### Scenario: Número de tarjeta en una queja
- **WHEN** una mención contiene "me cobraron doble en la tarjeta 4111 1111 1111 1111"
- **THEN** el texto enviado al clasificador y exportado contiene "me cobraron doble en la tarjeta [TARJETA]"

#### Scenario: Secuencia numérica que no es tarjeta
- **WHEN** una mención contiene un número de 16 dígitos que no supera la validación de Luhn
- **THEN** el sistema no lo marca como `[TARJETA]`

#### Scenario: Documento, teléfono y correo
- **WHEN** una mención contiene una cédula, un número de celular y un correo electrónico
- **THEN** cada uno se reemplaza por su marcador y la mención registra los tipos de dato detectados

### Requirement: Señal de exposición de datos sensibles
El sistema SHALL marcar las menciones en las que el autor expuso públicamente datos sensibles propios o de terceros, para que la entidad pueda gestionarlas.

#### Scenario: Cliente publica su número de cuenta
- **WHEN** se detecta al menos un dato sensible en una mención
- **THEN** la mención queda marcada con exposición de datos sensibles y con los tipos detectados, sin incluir los valores

### Requirement: Seudonimización de identificadores
Los identificadores de autor y las referencias de cliente que salen de la plataforma SHALL exportarse como seudónimos estables, derivados con una clave secreta y una función irreversible, y MUST ser consistentes entre exportaciones para que puedan correlacionarse.

#### Scenario: Mismo autor en dos menciones
- **WHEN** se exportan dos menciones del mismo autor
- **THEN** ambas llevan el mismo seudónimo de autor

#### Scenario: Seudónimo no reversible sin la clave
- **WHEN** un consumidor de la telemetría recibe un seudónimo de autor
- **THEN** no puede obtener el id externo original sin la clave secreta de la plataforma

### Requirement: Retención configurable
El sistema SHALL eliminar o anonimizar automáticamente las menciones y los perfiles de autor que superen el periodo de retención configurado por proyecto.

#### Scenario: Mención vencida
- **WHEN** una mención supera el periodo de retención de su proyecto
- **THEN** el proceso de retención elimina su texto original y los datos personales del autor, y conserva solo los datos agregados no identificables

### Requirement: Supresión por solicitud del titular
El sistema SHALL permitir, desde la API de administración, suprimir todos los datos personales asociados a un autor de un canal, y MUST registrar la operación de forma auditable.

#### Scenario: Solicitud de supresión
- **WHEN** un administrador solicita suprimir los datos de un autor
- **THEN** se eliminan su perfil, sus textos originales y su resultado de identidad, se registra un evento de auditoría sin datos personales y las futuras menciones de ese autor se siguen procesando como de un autor nuevo

### Requirement: Secretos fuera del código y de la telemetría
Las credenciales de las fuentes, del LLM y del microservicio de identidad, así como la clave de seudonimización, SHALL leerse de un gestor de secretos o del entorno, y MUST NOT aparecer en el código, en los logs de aplicación ni en la telemetría exportada.

#### Scenario: Error de autenticación con una fuente
- **WHEN** una fuente rechaza las credenciales
- **THEN** el error registrado identifica la fuente y el motivo sin incluir el valor de la credencial
