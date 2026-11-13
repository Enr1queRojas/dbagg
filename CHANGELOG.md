# Cambios

## Sin publicar — Ajustes tras la evaluación final (T25)

- El dominio modular `proveedores` solo añade `movimientos`; las operaciones compartidas siguen en
  `clientes.*`, que ya sirven a ambos catálogos. En T25 Jev repartía la probabilidad entre rutas
  equivalentes (`clientes.estado_cuenta` ↔ `proveedores.estado_cuenta`).
- «Del 30 de agosto a hoy» o un rango sin año que termina hoy: si el modelo pide el período, el
  servicio le devuelve una sola vez las fechas exactas (regla ya escrita en el prompt).

## Sin publicar — Dominio modular de productos

- `[modular] domains` acepta `productos` (apagado por defecto): rutas de existencias y ventas
  globales; el resto del inventario sigue en exploración acotada.

## Sin publicar — Dominio modular de bancos

- `[modular] domains` acepta `bancos` (apagado por defecto): rutas para saldo de una cuenta, saldos
  de todas y reporte de movimientos. Sin el dominio, los bancos siguen en el intérprete completo.

## Sin publicar — Dominio modular de proveedores

- `[modular] domains` acepta `proveedores` (apagado por defecto): rutas `proveedores.*` con
  `movimientos`, sin perder la pregunta de homónimos entre clientes y proveedores.

## Sin publicar — Experimento: prefijo estable

- Bandera `[modular] stable_prefix` (apagada): ordena el prompt modular de estable a variable y
  separa calendario y estado en un mensaje final. Pendiente de medición antes de adoptarla.
- Bandera `[modular] fixed_tool_schema` (apagada): esquema de herramienta igual para todo el dominio
  y la operación de la ruta exigida al validar. Pendiente de medición.
- Bandera `[modular] compact_history` (apagada): con una persona confirmada, el modelo recibe solo el
  último intercambio y el estado.

## Sin publicar — Prompt modular coherente con sus operaciones

- El prompt modular solo nombra las operaciones habilitadas en el turno.
- Con `[modular]`, si la ruta no permite lo pedido, el modelo puede responder
  `pedir_aclaracion(reason="otra_operacion")`: el servicio repite el turno una vez con el intérprete
  completo (`modular_fallback = "wrong_route"`), sin mostrarlo al usuario.

## Sin publicar — `system.md` generado desde los módulos (`catalog-domains-v7`)

- `src/dbagg/agent/templates/system.md` se genera con `python scripts/build_system_prompt.py` a
  partir de los módulos modulares; editar un módulo exige regenerarlo (`test_prompt_parity`).
- Módulos nuevos `domains/banks.md`, `domains/products.md`, `examples.md`, `shared/identity.md` y
  `shared/time.md`. El prompt completo incluye ahora las reglas de gastos y de `estado_cuenta`.
- Los prompts locales (`[prompts]` / `config/prompts/`) no cambian y siguen forzando el flujo anterior.

## Sin publicar — Instrucciones del enrutador

- Las instrucciones del enrutador (Jev y referencia) son una regla general breve; los casos viven en
  la descripción de cada ruta («usar cuando», «no usar cuando»). Se quitó el nombre propio. El
  enrutador recibe `previous_route` y `pending_clarification` como datos y la referencia ya no repite
  las instrucciones en la descripción de su herramienta.

## Sin publicar — Enrutamiento por niveles con Jev

- Campos opcionales `[modular] min_margin` y `min_domain_confidence`: operación si hay confianza y
  margen; si no, módulo del dominio con hasta tres operaciones; si no, intérprete completo sin
  segunda clasificación. El clasificador mini queda solo para errores de Jev. Sin los campos, nada
  cambia. Ver [docs/llm-configuration.md](docs/llm-configuration.md).

## Sin publicar — Respaldo de extracción con motivo

- Si la extracción modular propone una llamada fuera del contrato, el respaldo recibe esa llamada y
  el motivo del rechazo. Con `extraction_profile` igual al perfil activo no se crea un segundo cliente.

## Sin publicar — Continuidad tras el intérprete completo

- Con `[modular]`, un resultado verificado del intérprete completo (cliente o proveedor con código)
  conserva operación, identidad y período para el siguiente turno modular; antes se perdían.

## Sin publicar — Aclaraciones resueltas sin modelo

- Tras «Encontré varios clientes», responder con un código de la lista o su posición ejecuta la
  operación pendiente (con su período) sin llamar al modelo. Tras «¿Qué período?», «este mes»,
  «mes pasado», «esta semana» o «semana pasada» repiten la operación con el cliente confirmado.

## Sin publicar — Pre-enrutador sin llamadas

- Con `[modular]`, respuestas cortas («sí», «ok»), menciones de banco y nombres de cuentas junto a
  «efectivo», «cuenta» o «caja» van al intérprete completo sin llamar al enrutador
  (`local_decision = "pre_route_legacy"`). `route_is_consistent` sigue como red de seguridad.

## Sin publicar — Elegir de una lista con un número y códigos dictados

- Con una lista de personas abierta, un mensaje que es solo un número («3») elige esa fila. Un número
  dentro de una frase no elige; si el número es también un código de la lista, gana el código.
- Si un nombre no se encuentra, se reintenta una vez como código dictado por voz («M. Robles tres» →
  `M.ROBLES3`), solo con coincidencia exacta de código en un único catálogo; nunca por similitud.

## Sin publicar — Última nota de clientes

- `ultima_nota` ya existe para clientes: «última nota», «última compra», «última compra a crédito» o
  «nota de compra» de un cliente devuelven su última nota de crédito activa (`CREDITO_DATA`, folio,
  fecha, partidas e importe `SUM([TOTAL])`). Antes se respondía con su último pago. Solo todo el
  historial; con período, `fuera_alcance`. Sin notas pide aclaración.
- Prompt: `system.md`, `domains/customers.md`, `tools.json` y el módulo nuevo
  `operations/ultima_nota.md` (huella nueva de `catalog-domains-v6`). Ruta modular
  `clientes.ultima_nota`. Migración: si el servidor usa `config/prompts/`, copiar estos cambios.

## Sin publicar — Bancos: reporte tras el saldo y fechas

- «Dame un reporte de este mes» justo después de «Cuenta X» genera el PDF de esa cuenta, aunque el
  servidor use prompts propios en `config/prompts/`.
- `movimientos_banco` siempre es el PDF: sin fechas confirma la cuenta y pregunta «¿De qué fechas
  quieres el reporte?»; ya no lista el historial completo en texto.

## Sin publicar — Bancos: saldo corto y reporte en PDF

- `saldo_banco` responde solo la cuenta y su saldo actual; el desglose por concepto ya no se muestra
  (sigue avisando si no cuadra).
- `movimientos_banco` con período envía un PDF con todos los movimientos de la cuenta (fecha, concepto,
  folio, depósito, retiro), totales por concepto y saldo actual; en el chat solo va un resumen corto.
  Sin período sigue listando los últimos movimientos en texto.
- Migración en producción: si el servidor usa `config/prompts/`, copia ahí los cambios de bancos de
  `src/dbagg/agent/templates/system.md` y `tools.json`; reconstruir la imagen.

## Sin publicar — Despliegue automático por SSH

- Cada push a `main` con las pruebas en verde entra por SSH a la VM, actualiza el checkout desde git, reconstruye la
  imagen y reinicia el bot; si no queda sano, vuelve a la versión anterior
  (`.github/workflows/deploy.yml`, `scripts/deploy.sh`).
- Configuración en producción: deploy key de solo lectura en la VM, secretos `DEPLOY_*` en GitHub y
  `COMPOSE_PROFILES=tunnel` en `.env` tras el cambio de equipo. Guía en
  [docs/deploy-ssh.md](docs/deploy-ssh.md).

## Sin publicar — Consultas de bancos

- Saldo de una cuenta con su desglose por concepto, saldos de todas las cuentas sin sumar y
  movimientos de una cuenta por período (`saldo_banco`, `saldos_bancos`, `movimientos_banco`).
  Incluye efectivo de personas, caja chica y créditos; detalle en
  [docs/business-queries.md](docs/business-queries.md#bancos).
- Migración en producción, antes de desplegar: aplicar los `GRANT` nuevos de
  `scripts/sql/dbagg_readonly.sql` (12 tablas de bancos); confirmar que `SQL_ALLOWED_TABLES` incluye
  esas tablas; publicar con el paquete `config/prompts/agent-v7.md` y `config/prompts/tools.json`,
  que ya traen la sección de bancos.

## Sin publicar — Estado de cuenta en PDF

- Por WhatsApp, el bot aclara a la persona, pide fechas y entrega un PDF con el estado de cuenta de un
  cliente o proveedor, con un resumen aparte.
- Migración en producción: reconstruir la imagen con ReportLab; publicar los prompts de `config/prompts`
  en el paquete; probar el envío de documentos con el número real después de desplegar.

## Sin publicar — Consultas de proveedores

- Saldo, coincidencias, ranking, pagos, notas pendientes, última nota y movimientos de proveedores.
- El servicio decide si un nombre es de un cliente o de un proveedor, conserva ese contexto y pregunta
  si existe en ambos.
- Migración en producción, antes de desplegar: aplicar los `GRANT` nuevos de
  `scripts/sql/dbagg_readonly.sql` si el servidor usa ese script; sin ellos, las consultas de proveedores
  fallan aunque las de clientes sigan respondiendo; confirmar que `SQL_ALLOWED_TABLES` incluye `proadel.CATALOGO_PROVEEDORES_DATA_V` y
  `proadel.PAGO_PROVEEDOR_DATA`; publicar con el paquete los prompts de `config/prompts`.

## Sin publicar — Despliegue con contenedores

- `Dockerfile`, `docker-compose.yml` y `Caddyfile`: bot, Caddy y Cloudflare Tunnel, sin root,
  con sistema de solo lectura y sin abrir puertos de entrada.
- `scripts/package_deploy.py` genera un paquete para otra máquina, sin `.env` ni `data/`;
  guía en [docs/deploy-vm.md](docs/deploy-vm.md).
- `scripts/update_webhook_url.py` comprueba `/webhook` en vez de `/health`, que puede no ser público.

## Sin publicar — Aviso a números no autorizados

- Un número fuera de `WHATSAPP_ALLOWED_NUMBERS` recibe un texto fijo que le pide contactar a
  soporte, sin pasar por el modelo ni el ERP. Una vez al día por número, máximo 30 por hora.

## Sin publicar — Correcciones del outbox

- La recuperación al arrancar reenvía la misma fila y la cierra; antes creaba otra y repetía la
  respuesta en cada reinicio. Una pendiente de más de 1 hora se cierra sin enviarse, para que un
  saldo antiguo no llegue como actual.
- Un 4xx de Meta marca la fila como fallida en lugar de dejarla pendiente.
- `DATA_RETENTION_DAYS` también limpia el outbox, y la deduplicación se olvida a los 8 días.
- Límite de frecuencia persistente igual al de memoria: los comandos y los mensajes rechazados no
  alargan la espera.

## Sin publicar — Selección tras una lista y plantilla del prompt

- Tras un ranking o una lista de varios clientes, una operación por cliente que no nombra ni
  elige a uno (`el segundo`, un nombre o código) pide aclarar el cliente; antes el modelo podía
  continuar con la primera fila y responder sobre otra persona.
- `templates/system.md`: comprobaciones previas a `consultar_negocio` (estado de cuenta, pedidos
  compuestos, días sin mes, más de 10 registros, listas sin elegir, código identificado). En
  `evaluation.routing` con `gpt-4.1-mini` pasa de 30/42 a 41/42 casos estables.
- Los prompts locales no cambian al actualizar: ver [diseño del prompt](docs/prompt-design.md).
- «El segundo» se resuelve con el código de esa fila, sin depender del modelo ni de homónimos, y
  una aclaración de cliente no cierra la lista pendiente.
- Si el modelo no llama herramientas en el primer turno, se le pide una decisión una vez más.
- Pedir aclarar el período cuando la pregunta ya trae mes y año recibe una corrección.

## Sin publicar — Evaluación de enrutamiento

- `python -m dbagg.evaluation.routing` mide la elección de herramienta del modelo real con
  casos sintéticos, sin consultar la base.
- `intent-routing.json` pasa de 15 a 42 casos; CI exige que cubra todas las operaciones y
  motivos de aclaración.
- Las pruebas pasan en Windows: E/S explícita en UTF-8 y conexión SQLite cerrada.

## Sin publicar — Coincidencias de clientes

- Una coincidencia exacta ya no detiene la búsqueda: si «Segura» es el código de un cliente
  y el apellido de otro, se listan ambos y `saldo` pide elegir en lugar de responder con uno.
- `codigo_exacto` selecciona un código ya elegido; el servicio lo ignora si ese valor no
  aparece como código en la pregunta o en una respuesta previa.
- Nueva operación `contar_coincidencias`: total real por `COUNT` e identificación sin saldos.

## Sin publicar — Endurecimiento de seguridad

Detalle en [docs/security.md](docs/security.md).

- `validate_sql`: un alias ya no habilita columnas fuera del catálogo; se rechazan
  variables (`@x`, `@@VERSION`), `JOIN` sin `ON a.col = b.col` entre objetos distintos y
  más de tres referencias a objetos.
- Webhook: mensajes con `timestamp` de más de 15 minutos o inválido no se responden;
  `hub.verify_token` se redacta en el log de acceso; las notas de voz solo se descargan
  de hosts HTTPS de Meta.
- La primera conexión SQL rechaza logins con permisos de escritura o administración.
  `scripts/sql/dbagg_readonly.sql` crea un login con `SELECT` solo sobre el catálogo.
- Retención `DATA_RETENTION_DAYS` (90 días) para preguntas sin respuesta y valoraciones,
  y comando `purge --days`.
- Dependencias: FastAPI 0.162.0, starlette 1.10.0 (13 avisos de seguridad en 0.46.2) y
  certifi ≥ 2026.10.19; `pip-audit` en `dev`.

## Sin publicar — Notas de voz de WhatsApp

- Los números autorizados pueden enviar notas de voz de hasta 60 s. Se descargan de Meta
  en memoria, se transcriben con `gpt-4o-mini-transcribe` y siguen el mismo flujo que un
  texto, con eco «🎙️ Entendí: …» antes de la respuesta.
- Sección opcional `[transcription]` en `llm.toml`; sin la clave, las notas de voz se
  rechazan con aviso y el texto sigue funcionando.
- Operación verificada `notas_pendientes`: notas de crédito abiertas que forman el saldo
  actual, con asignación FIFO de pagos y devoluciones (saldo inicial primero) y
  comprobación contra `[SALDO ACTUAL]`. Fórmula del saldo verificada en 423/423 clientes.
- Si una consulta de cliente no encuentra a nadie, el modelo recibe una pista y un
  reintento (puede ser un apartado de gastos, producto o almacén). `hint` opcional en
  `[transcription]` para nombres frecuentes.
- Registro opcional de preguntas sin respuesta (`GAP_LOG_ENABLED=true`, `data/gaps.sqlite3`):
  fuera de alcance, sin evidencia, límite de consultas y exploratorias. CLI `gaps-summary`,
  `gaps-list`, `gaps-review` y `gaps-export` para convertirlas en casos sintéticos; CI valida
  `evals/cases` contra herramientas, operaciones y motivos vigentes.
- Una transcripción nunca ejecuta comandos. Audio y transcripciones no se guardan ni
  aparecen en los logs. Pruebas con audio Ogg sintético y dobles HTTP; no validan Meta
  ni el modelo de transcripción reales.

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
