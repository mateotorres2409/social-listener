# Spec Delta

## Purpose

Tratar cada voz de forma individual: consolidar la identidad de un autor dentro de cada canal, mantener su perfil e historial y reconstruir las conversaciones en las que participa.

## ADDED Requirements

### Requirement: Resolución de autor por canal
El sistema SHALL mantener un único perfil de Autor por par (canal, id externo del autor) y MUST asociarle cada mención que publique. Un cambio de username en el canal MUST actualizar el perfil existente sin crear uno nuevo.

#### Scenario: Primera mención de un autor
- **WHEN** llega una mención de un autor sin perfil
- **THEN** el sistema crea su perfil y le asocia la mención

#### Scenario: Cambio de username
- **WHEN** llega una mención de un autor ya conocido con un username distinto
- **THEN** el perfil conserva su identificador, actualiza el username vigente y guarda el anterior en su historial

### Requirement: Perfil e historial del autor
El perfil del autor SHALL incluir: username vigente y anteriores, nombre visible, seguidores, verificado, primera y última mención, total de menciones, distribución por tipo y sentimiento acumulado ponderado por recencia.

#### Scenario: Actualización del perfil
- **WHEN** se clasifica una nueva mención de un autor
- **THEN** su total de menciones, su distribución por tipo y su sentimiento acumulado se actualizan

#### Scenario: Consulta del historial
- **WHEN** un analista consulta un autor desde la API de administración
- **THEN** obtiene su perfil y sus menciones ordenadas por fecha, con sus clasificaciones

### Requirement: Detección de autores recurrentes
El sistema SHALL marcar como recurrente al autor que supere un número configurable de quejas o reclamos en una ventana de tiempo configurable, y MUST incluir esa marca en las menciones que exporte.

#### Scenario: Autor con quejas repetidas
- **WHEN** un autor publica más quejas que el umbral dentro de la ventana
- **THEN** su perfil queda marcado como recurrente y sus siguientes menciones se exportan con esa marca

### Requirement: Reconstrucción de hilos
El sistema SHALL agrupar en un Hilo las menciones relacionadas por respuesta (padre e hijo) dentro de un canal, y MUST asociar al hilo una mención que llegue antes que su padre en cuanto este llegue.

#### Scenario: Respuesta a una publicación
- **WHEN** llega una mención que responde a otra ya ingerida
- **THEN** ambas comparten el mismo identificador de hilo

#### Scenario: Respuesta que llega antes que el padre
- **WHEN** llega primero una respuesta y después la publicación original
- **THEN** al llegar la publicación, ambas quedan en el mismo hilo

### Requirement: Participación de la cuenta oficial
El sistema SHALL identificar las menciones publicadas por las cuentas oficiales configuradas para el proyecto y MUST marcar los hilos en los que la entidad ya respondió.

#### Scenario: La entidad responde una queja
- **WHEN** una cuenta oficial responde dentro de un hilo
- **THEN** el hilo queda marcado como atendido por la entidad y las menciones de la cuenta oficial no se envían a validación de identidad
