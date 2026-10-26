# Cambios

## Sin publicar — Catálogo de fuentes SQL y prompt v6

- `src/dbagg/database/catalog.json` declara 26 fuentes de clientes, gastos e inventarios
  con dominio, estado, acceso y columnas permitidas. `SQL_ALLOWED_TABLES` solo puede
  acotarlo: los objetos fuera del catálogo se ignoran con advertencia al arrancar.
- Con catálogo, el validador exige un objeto por consulta (salvo relación declarada),
  columnas explícitas catalogadas y rechaza `SELECT *`. Direcciones, teléfonos,
  responsables y choferes no se catalogan; `describir_tablas` los oculta.
- El modelo recibe fichas por dominio en lugar de la lista de nombres. `last_trace`
  registra `catalog_version`. Prompt `catalog-domains-v6`; los prompts locales se
  conservan y deben incorporar el párrafo de exploración a mano.
- Nueva operación `saldos_coincidencias`: saldo de cada homónimo por separado, sin sumar.
  `ranking_deuda` solo se acepta si la pregunta pide un ranking («más deben», «top»,
  «mayores»…); si no, el modelo recibe una indicación y un segundo intento. Corrige que
  «los saldos de los 3 <nombre>» devolviera el ranking global.
- Columnas tomadas de metadatos de SQL Server; su significado contable sigue pendiente.
  Las pruebas usan dobles: no validan SQL Server ni un modelo real.

## Sin publicar — Mantenimiento del checkout

- Limpieza con vista previa de cachés y restos de la estructura antigua; conserva
  archivos versionados, configuración, datos y enlaces.
- Guías actualizadas para usar main o la rama del PR, sin instrucciones de recrear
  ramas eliminadas; ejemplos de contexto apuntan a archivos vigentes.
- CI ejecuta los pushes de todas las ramas, además de los pull requests.

## 0.3.1 — Claridad del agente y prompt v5

- El bucle del agente delega consultas de negocio, inspección, SQL y presentación en
  métodos explícitos de la misma clase. Sin nuevas capas ni dependencias.
- Argumentos JSON que no son objetos y listas de tablas con elementos inválidos se
  rechazan antes de acceder a la base; los límites y controles SQL se conservan.
- Prompt v5 organizado por decisión, identidad, tiempo, alcance y exploración;
  ejemplos sintéticos y descripciones precisas de herramientas. No añade operaciones.
- Casos de evaluación para cambios de tema y reportes no soportados. La mejora de
  interpretación requiere comparación con el modelo real; no se deduce de los mocks.
- Los prompts locales existentes se conservan. Actualización y reversión en
  `docs/prompt-design.md`.

## 0.3.0 — Proveedores LLM y prompts configurables

- Perfiles TOML para OpenAI, GLM, Gemini, Claude y otros endpoints compatibles con OpenAI.
  El agente consume un contrato común; los adaptadores preservan llamadas a herramientas
  y firmas necesarias para continuar el turno. No hay fallback automático de proveedor.
- `dbagg-llm init/check` crea y valida configuración local sin copiar ni imprimir claves.
  Sin archivo local se mantiene la configuración OpenAI anterior.
- Prompts y descripciones editables en archivos UTF-8; carga por pregunta y huella real
  `customer-reports-v4:<hash>`. Los permisos, esquemas y controles SQL siguen en código.
- Valoraciones con proveedor/perfil/modelo y huella de prompt. Migración SQLite aditiva
  que conserva valoraciones y revisiones existentes.
- Pruebas HTTP simuladas para los cuatro proveedores; disponibilidad de modelos y
  credenciales de las cuentas requieren validación real.

## 0.2.3 — Correcciones de la validación previa al merge

- El ranking conserva filas con nombre/código faltante y muestra una advertencia de
  identificación incompleta. Nombres largos o con espacios de relleno ya no invalidan
  toda la respuesta. El mensaje se limita por filas completas, sin cambiar importes.
- Ante «cuál de los tres tiene saldo», el agente explica que la comparación de ese grupo
  no está implementada y pide consultar cada código; no elige uno arbitrariamente.
  Se agrega una guarda para esa forma de pregunta y orientación al modelo para otras variantes.
- Prompt `customer-reports-v3` y regresiones con datos sintéticos. Repetir ranking en
  SQL Server real: el mensaje anterior no distinguía vacío, nulo o longitud excesiva.

## 0.2.2 — Segunda entrega de refactor

- Cinco operaciones verificadas para saldo, ranking y pagos por cliente; SQL parametrizado,
  comprobación de esquema, homónimos y códigos duplicados.
- Respuestas financieras desde datos SQL; se descarta prosa libre del modelo. Otras
  consultas conservan una salida explícitamente exploratoria, sin validar su semántica contable.
- Cobranza aplica `ACTIVA`, `FECHA`, períodos explícitos y empates del último pago;
  informa datos incompletos y límites sin sumar muestras ni inventar ceros.
- Calendario del negocio con `BUSINESS_TIMEZONE`, predeterminado `America/Mexico_City`.
- Prompt `customer-reports-v2`, regresiones sintéticas de negocio y seguimiento por webhook.
  La selección de intención por el modelo y SQL Server remoto requieren validación real.

## 0.2.1 — Primera entrega de refactor

- Paquete Python movido a `src/dbagg/`; comandos anteriores y `.env` de la raíz compatibles.
- Saldo desconocido/inválido y falta de fechas ya no producen riesgo verde; el HTML
  presenta el motivo y mantiene visibles los saldos desconocidos.
- Cobranza usa `FECHA` y conversión 103, limita el historial al corte y detecta fechas
  inválidas. El saldo es vigente; no se reconstruyen saldos históricos.
- TOP pequeños se conservan, grandes se acotan. OFFSET, PERCENT, WITH TIES y opciones
  de ejecución no soportadas se rechazan explícitamente.
- Un error entregado invalida la valoración de la respuesta anterior.
- Nuevas regresiones de cálculo, HTML/JavaScript y feedback; CI Windows/Linux y prueba
  de instalación del wheel fuera del checkout.

Quedan pendientes las entregas de cola persistente, evaluación y operación descritas
en `docs/refactor-roadmap.md`. La publicación del código no habilita producción.
