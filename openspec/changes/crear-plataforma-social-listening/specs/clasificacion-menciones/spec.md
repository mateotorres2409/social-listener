# Spec Delta

## Purpose

Asignar a cada mención seis dimensiones de clasificación (canal, impacto, tipo, sentimiento, categorización y temática) con criterios propios de una entidad financiera, con trazabilidad del clasificador y degradación controlada ante fallas.

## ADDED Requirements

### Requirement: Clasificación completa en seis dimensiones
Toda mención que supere la etapa de protección de datos SHALL quedar clasificada en las seis dimensiones: canal, impacto, tipo, sentimiento, categorización y temática. Ninguna mención MUST exportarse con una dimensión sin valor; cuando no se pueda determinar, el valor es `indeterminado`.

#### Scenario: Clasificación exitosa
- **WHEN** una mención termina la etapa de clasificación
- **THEN** tiene un valor en cada una de las seis dimensiones

### Requirement: Trazabilidad de cada dimensión
Cada valor de dimensión SHALL registrar su origen (`regla`, `llm`, `respaldo` o `manual`), el nombre y la versión del clasificador y una confianza entre 0 y 1. Cuando el origen es `llm`, MUST registrar además el proveedor y el modelo que produjeron el valor.

#### Scenario: Auditoría de una clasificación
- **WHEN** se consulta una mención clasificada
- **THEN** cada dimensión muestra su valor, su origen, la versión del clasificador y su confianza

#### Scenario: Clasificación por un modelo
- **WHEN** una dimensión fue clasificada por un modelo de lenguaje
- **THEN** la clasificación indica el proveedor (p. ej. `anthropic`, `ollama`, `openai_compatible`) y el identificador del modelo usado

### Requirement: Dimensión canal
El canal SHALL derivarse de forma determinista de la fuente y del formato de la mención: red social (p. ej. `x`, `facebook`, `instagram`, `tiktok`, `youtube`, `reddit`, `resenas`) y formato (`publicacion`, `comentario`, `respuesta`, `resena`).

#### Scenario: Respuesta en una red social
- **WHEN** una mención de Instagram es la respuesta a un comentario
- **THEN** su canal es `instagram` con formato `respuesta`, origen `regla` y confianza 1

### Requirement: Dimensión tipo
El tipo SHALL tomar un único valor de este catálogo: `peticion`, `queja`, `reclamo`, `sugerencia`, `felicitacion`, `consulta`, `alerta_fraude`, `riesgo_reputacional`, `informativa` o `irrelevante`. Los cuatro primeros se alinean al régimen de PQRS.

#### Scenario: Reclamo por un cobro
- **WHEN** una mención dice "me cobraron una cuota de manejo que no autoricé y exijo la devolución"
- **THEN** su tipo es `reclamo`

#### Scenario: Posible suplantación
- **WHEN** una mención reporta un perfil falso que pide claves a nombre de la entidad
- **THEN** su tipo es `alerta_fraude`

### Requirement: Dimensión sentimiento
El sentimiento SHALL expresarse como una etiqueta (`positivo`, `neutral`, `negativo` o `mixto`) y un puntaje entre -1 y 1 coherente con la etiqueta.

#### Scenario: Comentario negativo
- **WHEN** una mención expresa enojo por una falla de la app
- **THEN** su etiqueta es `negativo` y su puntaje es menor que 0

### Requirement: Dimensión categorización con taxonomía configurable
La categorización SHALL asignar una categoría y, si aplica, una subcategoría de la taxonomía activa del proyecto. El sistema MUST incluir una taxonomía financiera por defecto, editable desde la API de administración, y MUST versionar cada cambio de taxonomía.

#### Scenario: Taxonomía por defecto
- **WHEN** un proyecto no define una taxonomía propia
- **THEN** se clasifica con la taxonomía por defecto, que cubre al menos: tarjetas, cuentas, créditos, inversiones, seguros, canales digitales, oficinas y cajeros, atención al cliente, fraude y seguridad, tarifas y costos, y reputación corporativa

#### Scenario: Valor fuera de la taxonomía
- **WHEN** el clasificador propone una categoría que no existe en la taxonomía activa
- **THEN** el sistema la descarta, asigna `indeterminado` y la mención queda marcada para revisión

#### Scenario: Cambio de taxonomía
- **WHEN** un administrador publica una nueva versión de la taxonomía
- **THEN** las nuevas menciones se clasifican con esa versión y las ya clasificadas conservan la versión con que se clasificaron

### Requirement: Dimensión temática
La temática SHALL describir el asunto concreto de la mención con una etiqueta corta normalizada (p. ej. `caida-app`, `cobro-cuota-manejo`) y MUST reutilizar una temática existente del proyecto cuando sea equivalente, en lugar de crear una nueva.

#### Scenario: Temática recurrente
- **WHEN** varias menciones hablan de que la app no deja iniciar sesión
- **THEN** todas comparten la misma etiqueta de temática

### Requirement: Dimensión impacto
El impacto SHALL calcularse de forma determinista como `bajo`, `medio`, `alto` o `critico`, a partir del alcance del autor, las interacciones, el sentimiento, el tipo y la categoría, con umbrales configurables por proyecto. Toda `alerta_fraude` y todo `riesgo_reputacional` MUST tener impacto al menos `alto`.

#### Scenario: Influenciador con queja
- **WHEN** un autor que supera el umbral de seguidores publica una queja negativa
- **THEN** el impacto es al menos `alto`

#### Scenario: Alerta de fraude de bajo alcance
- **WHEN** un autor con pocos seguidores reporta una alerta de fraude
- **THEN** el impacto es al menos `alto`

#### Scenario: Felicitación de bajo alcance
- **WHEN** un autor con pocos seguidores publica una felicitación
- **THEN** el impacto es `bajo`

### Requirement: Envío exclusivo de texto enmascarado al clasificador
Todo modelo de lenguaje, sea externo o local, SHALL recibir únicamente el texto enmascarado y los metadatos no identificables de la mención. MUST NOT recibir el texto original, ni identificadores del autor, ni resultados de identidad.

#### Scenario: Mención con datos sensibles
- **WHEN** una mención con un número de tarjeta se envía a un modelo de lenguaje
- **THEN** la solicitud contiene el marcador `[TARJETA]` y no el número

#### Scenario: Modelo local
- **WHEN** la mención se clasifica con un modelo desplegado en la infraestructura de la entidad
- **THEN** la solicitud también contiene solo el texto enmascarado

### Requirement: Proveedor y modelo configurables por proyecto
El sistema SHALL permitir configurar por proyecto, sin cambiar código, una cadena ordenada de modelos de lenguaje (proveedor, modelo y tiempo límite de cada uno). MUST soportar al menos Anthropic (Claude), Ollama y cualquier proveedor compatible con la API de OpenAI, y MUST permitir desactivar los modelos de lenguaje para un proyecto.

#### Scenario: Proyecto con modelo local
- **WHEN** un proyecto se configura con Ollama como único proveedor
- **THEN** sus menciones se clasifican con el modelo local configurado, sin llamar a proveedores externos

#### Scenario: Proyectos con proveedores distintos
- **WHEN** un proyecto usa Claude y otro usa un proveedor compatible con OpenAI
- **THEN** cada mención se clasifica con la cadena de su propio proyecto

#### Scenario: Modelos de lenguaje desactivados
- **WHEN** un proyecto no tiene ningún modelo configurado
- **THEN** sus menciones se clasifican solo con reglas y las dimensiones correspondientes quedan con origen `respaldo`

#### Scenario: Proveedor no soportado
- **WHEN** se configura un proyecto con un proveedor desconocido
- **THEN** la configuración se rechaza con un error de validación y la cadena anterior sigue vigente

### Requirement: Clasificación uniforme entre proveedores
Todos los proveedores SHALL recibir las mismas definiciones de dimensiones, la misma taxonomía activa y el mismo catálogo de temáticas, y sus respuestas MUST validarse contra los mismos catálogos y esquemas, de modo que el resultado tenga la misma estructura sin importar el modelo.

#### Scenario: Cambio de proveedor
- **WHEN** un proyecto cambia de Claude a Ollama
- **THEN** las nuevas clasificaciones usan los mismos valores posibles por dimensión y la misma estructura

### Requirement: Degradación ante fallas del modelo
Si un modelo de la cadena no responde, excede su tiempo límite o devuelve una salida inválida, el sistema SHALL intentar con el siguiente de la cadena. Si todos fallan, MUST clasificar con las reglas de respaldo, registrar el origen `respaldo` y continuar el pipeline sin bloquearlo.

#### Scenario: Falla del proveedor primario
- **WHEN** el primer modelo de la cadena devuelve errores y el segundo responde correctamente
- **THEN** la mención se clasifica con el segundo modelo y la clasificación registra ese proveedor y modelo

#### Scenario: Todos los modelos no disponibles
- **WHEN** todos los modelos de la cadena fallan durante la clasificación
- **THEN** la mención se clasifica con las reglas de respaldo y se exporta normalmente

#### Scenario: Salida inválida
- **WHEN** un modelo devuelve una respuesta que no cumple el esquema esperado, incluso después de un reintento de corrección
- **THEN** el sistema la descarta, continúa con el siguiente de la cadena o con el respaldo, y registra el evento en la telemetría

### Requirement: Marcado para revisión humana
Toda mención con alguna dimensión de confianza menor al umbral configurable del proyecto SHALL quedar marcada para revisión, y la API de administración MUST permitir corregir sus dimensiones registrando el origen `manual`.

#### Scenario: Corrección manual
- **WHEN** un analista corrige la categoría de una mención marcada para revisión
- **THEN** la dimensión queda con origen `manual`, confianza 1 y se emite una actualización de la mención por OpenTelemetry
