# Tareas: mejorar respuestas y optimizar el flujo modular con Jev

> **Para el agente ejecutor:** este documento es autosuficiente. Ejecuta las
> tareas en orden, una por commit, marcando cada casilla `- [ ]` al terminar.
> El diagnóstico que las justifica está en
> `2026-11-12-mejora-prompts-enrutamiento-jev.md`; léelo antes de empezar.

**Objetivo:** que el agente responda bien más veces a la primera y que cada
mensaje cueste menos, medido como **costo por consulta correcta**.

**Arquitectura actual (resumen):** `Assistant.answer` (`src/dbagg/agent/service.py`)
recibe pregunta, historial y `ConversationState`. Si `[modular].enabled`, llama a
`_select_modular`: un enrutador (`JevRouter` o `ReferenceRouter`, en
`src/dbagg/routing/`) elige una ruta del registro (`routing/registry.py`);
`select_context` (`agent/context_builder.py`) arma un prompt reducido con
`templates/core.md` + `domains/*.md` + `operations/*.md` y restringe las
herramientas. Si no hay ruta, se usa el prompt completo `templates/system.md`
(«legacy»). El servicio valida identidad y parámetros y ejecuta SQL parametrizado;
el modelo nunca produce cifras.

**Stack:** Python 3.11+, `unittest`, ruff, SQLite sintético para pruebas, SQL
Server en producción (pyodbc), OpenAI-compatible para el modelo, TypeSafe Jev
para enrutar.

## Decisiones ya tomadas por el dueño del producto

1. El conjunto de evaluación final usa la redacción real del piloto, anonimizada.
2. La métrica que manda es el costo por consulta correcta, no la latencia.
3. Clientes, proveedores, bancos y productos tienen la misma importancia.
4. «Última nota / última compra» **debe existir para clientes**. El saldo de un
   cliente es el balance entre sus notas de crédito y sus notas de cobranza, así
   que la última compra de un cliente es su última nota de crédito.

## Reglas globales

- Rama de trabajo: la que indique la sesión. Sin merge ni PR salvo que se pida.
- Todo cambio de comportamiento queda detrás de `[modular]` o de una bandera
  nueva, desactivada por defecto. No tocar `config/llm.toml` local ni el
  despliegue. `src/dbagg/llm/default.toml` conserva `enabled = false`.
- Las cifras salen de SQL, nunca del modelo. Toda consulta va parametrizada.
- Ningún atajo determinista puede ampliar permisos: solo decide qué operación
  ya autorizada se ejecuta, y el servicio vuelve a validar identidad y contexto.
- Datos reales nunca entran al repo. Los casos de `evals/cases/` son
  `data_kind: "synthetic"` con identidades ficticias (`tests/unit/test_eval_cases.py`
  lo exige).
- No debilitar, omitir ni borrar pruebas para que pasen. Si una prueba existente
  contradice una tarea, detente y repórtalo.
- `evals/cases/whatsapp-pilot.json` es el conjunto de aceptación: **no ajustar
  reglas mirando sus casos uno por uno**. Para desarrollar usa
  `modular-development.json`, `intent-routing.json` y `bank-routing.json`.
- Antes de cada commit, con resultado limpio:

  ```bash
  python -m ruff check src tests scripts whatsapp_agent.py score_riesgo.py business_context.py
  ```

  ```bash
  python -m ruff format --check src tests scripts whatsapp_agent.py score_riesgo.py business_context.py
  ```

  ```bash
  python -m unittest discover -s tests -v
  ```

- Las tareas marcadas **[API]** consumen `OPENAI_API_KEY` y `TYPESAFE_API_KEY`.
  Si no están disponibles, deja el comando listo, no inventes resultados y
  anótalo como pendiente en el informe final.
- Si cambia un prompt, cambia su huella: registra versión y alcance en
  `CHANGELOG.md` y `docs/prompt-design.md`.

## Preparación

- [x] **T0. Entorno y línea base de pruebas**
  - Instalar: `python -m pip install -r requirements.txt -e '.[dev]'`. En Linux
    `pyodbc` necesita `unixodbc` (`apt-get install -y unixodbc`).
  - Ejecutar la suite completa y anotar el número de pruebas. Si algo falla
    antes de tocar código, repórtalo y no sigas.
  - **Resultado (2026-11-12):** 623 pruebas en verde con Python 3.13 tras instalar
    `unixodbc`. `ruff check` limpio; `ruff format` reformateó 5 archivos
    preexistentes (solo formato, sin cambio de comportamiento).

---

## Bloque A — Medición (requisito de los bloques C a F)

- [x] **T1. Métricas separadas en el evaluador**
  - **Archivos:** `src/dbagg/evaluation/modular.py`, `tests/unit/test_routing_eval.py`.
  - **Qué hacer:** `evaluate()` devuelve hoy un único `passed`. Añadir por caso:
    `route_expected` (derivada de `expected_tool` + `expected_arguments.operation`
    con el registro), `route_selected`, `route_correct`, `arguments_correct`
    (solo cuando la ruta fue correcta o se usó legacy), `calls` por tarea
    (`routing`, `extraction`, `interpretation`, respaldos), tokens de entrada,
    salida y caché, y `duration_ms`. El resumen agrega por variante y por
    `domain` del caso: acierto de ruta, acierto de parámetros dada la ruta,
    llamadas por turno, tokens por turno, costo conocido, **costo por consulta
    correcta**, p50/p95 de latencia.
  - Aceptar `--dataset` varias veces y `--repeat N`.
  - **Pruebas:** con proveedor y enrutador simulados, un caso de ruta correcta
    con parámetros malos y uno de ruta mala se contabilizan en la columna
    correcta; el costo por consulta correcta es `null` si no hay aciertos.
  - **Hecho cuando:** el informe JSON y HTML muestran las cuatro métricas por
    variante y dominio.

- [x] **T2. Calibración de Jev**
  - **Archivos:** crear `src/dbagg/evaluation/calibration.py`; prueba nueva
    `tests/unit/test_calibration.py`.
  - **Qué hacer:** a partir de los resultados de T1 (que ya guardan
    `probabilities` por caso), producir: matriz de confusión ruta esperada ×
    ruta elegida; curva cobertura/acierto para umbrales de confianza de 0.50 a
    0.95; la misma curva usando margen (primera − segunda probabilidad); y
    acierto a nivel dominio (suma de probabilidades de las rutas del dominio).
    Salida JSON y una tabla en texto.
  - **Pruebas:** con una lista fija de decisiones sintéticas, la matriz y las
    curvas dan valores calculados a mano.
  - **Hecho cuando:** `python -m dbagg.evaluation.calibration --input <report.json>`
    genera el informe sin llamar a ninguna API.

- [ ] **T3. Línea base [API]**
  - Ejecutar T1 con variantes `legacy`, `reference` y `jev`, sobre
    `whatsapp-pilot.json`, `intent-routing.json` y `bank-routing.json`, con
    `--repeat 3`. Guardar en `docs/evaluations/pilot-baseline/` (sin contenido
    de preguntas reales; los casos ya son ficticios). Ejecutar T2 sobre el
    resultado.
  - **Hecho cuando:** existe `docs/evaluations/pilot-baseline/summary.md` con la
    tabla de métricas y la lista de casos fallidos por variante.
  - **Pendiente (2026-11-12):** sin claves de OpenAI/Jev ni salida de red en la
    sesión. Comandos listos en `docs/evaluations/pilot-baseline/PENDIENTE.md`.

---

## Bloque B — Respuestas incorrectas detectadas en el piloto

- [x] **T4. `ultima_nota` para clientes**
  - **Contexto:** hoy `ultima_nota` es solo de proveedores
    (`SUPPLIER_ONLY_OPERATIONS` en `business/suppliers.py`, implementada en
    `SupplierQueries._last_note` sobre `COMPRAS_DATA`). En el piloto «última
    nota del cliente X» se respondió con su último pago.
  - **Archivos:** `src/dbagg/business/customers.py`, `business/suppliers.py`,
    `agent/service.py`, `agent/prompts.py`, `agent/templates/system.md`,
    `templates/domains/customers.md`, `templates/tools.json`, crear
    `templates/operations/ultima_nota.md`, `routing/registry.py`,
    `docs/business-queries.md`; pruebas en `tests/unit/test_customer_queries.py`,
    `test_supplier_routing.py`, `test_modular_context.py`.
  - **Qué hacer:**
    - Añadir `ultima_nota` a `OPERATIONS` de clientes y dejar
      `SUPPLIER_ONLY_OPERATIONS = ("movimientos",)`.
    - Implementar `CustomerQueries` para la operación con `CREDITS`
      (`proadel.credito_data`): filas `ESTADO = 'ACTIVA'` del cliente, fecha con
      `date_expression(catalog[CREDITS]["FECHA"], ...)`, la nota es el `FOLIO`
      de la última fecha válida; importe = `SUM([TOTAL])` por folio (clientes
      usan `TOTAL`, proveedores `IMPORTE`). Seguir la forma de respuesta de
      `SupplierQueries._last_note` (encabezado con nombre y código, fecha,
      folio, renglones, total, aviso de moneda sin confirmar). Sin notas:
      respuesta `needs_clarification` explícita, no un cero.
    - Revisar cómo `_pending_notes` de clientes usa `CREDITS` (línea ~391 de
      `customers.py`) y reutilizar sus comprobaciones de columnas (`_require`).
    - Solo `period = todo`; con período → `fuera_alcance`, igual que `ultimo_pago`.
    - Prompts: «última nota», «última compra», «última compra a crédito» y
      «nota de compra» de una persona → `ultima_nota`. Una nota **no** es un
      pago: nunca `ultimo_pago`. Quitar «solo de proveedores» para `ultima_nota`.
    - Ruta modular `clientes.ultima_nota` con descripción en `DESCRIPTIONS` y
      `CREDITS` entre sus tablas.
    - Quitar en `service.py` el mensaje «solo disponible para proveedores»
      cuando la operación es `ultima_nota` (sigue aplicando a `movimientos`).
  - **Pruebas:** SQLite sintético con dos notas activas en fechas distintas, una
    cancelada posterior y un folio con varios renglones: devuelve la nota activa
    más reciente con el total sumado; cliente sin notas; período rechazado;
    proveedor sigue usando `COMPRAS_DATA`/`IMPORTE`.
  - **Hecho cuando:** los cinco casos `pilot-*` que esperan `ultima_nota` son
    coherentes con el contrato y `test_eval_cases` pasa.
  - **Resultado:** `CustomerQueries._last_note` sobre `CREDITO_DATA`; ruta
    `clientes.ultima_nota` (tablas de crédito y compras); seis casos `pilot-*`
    esperan `ultima_nota` y son coherentes (se actualizó el criterio obsoleto
    «pendiente de decisión»). 630 pruebas en verde.

- [x] **T5. Elegir de una lista con un número solo y con códigos dictados**
  - **Archivos:** `src/dbagg/agent/intent.py` (`list_position`),
    `agent/entity.py`, `agent/service.py`; pruebas en
    `tests/unit/test_list_selection.py`, `test_entity.py`.
  - **Qué hacer:**
    - `list_position` acepta un mensaje que es **solo** un número de 1 a 2
      cifras (con signos o espacios alrededor) cuando hay una lista abierta
      (`_open_list`). Un número dentro de una frase sigue sin contar. Si el
      número coincide además con un código de la lista, gana el código.
    - Código dictado por voz: si la búsqueda literal de `customer` no encuentra
      nada, reintentar **una vez** con una forma normalizada (números en
      palabra → dígitos, sin espacios: «A. Robles tres» → `A.ROBLES3`) como
      código exacto. Solo se acepta si coincide exactamente con un código
      existente; nunca por similitud.
  - **Pruebas:** «3» tras lista numerada elige la fila 3; «3» sin lista no elige
    nada; «tengo 3 dudas» no elige; código dictado resuelve a un código
    existente y uno inexistente sigue pidiendo aclaración.
  - **Resultado:** `list_position(question, codes)`; `dictated_code` en
    `entity.py`; reintento único `Assistant._dictated_code_answer` (no cubre el
    estado de cuenta en PDF, que resuelve la identidad por su cuenta).

---

## Bloque C — Quitar llamadas que no aportan

- [x] **T6. Pre-enrutador determinista**
  - **Archivos:** `src/dbagg/routing/policy.py`, `agent/service.py`
    (`_select_modular`); pruebas en `tests/unit/test_modular_context.py`.
  - **Qué hacer:** hoy `route_is_consistent` veta después de llamar al
    enrutador (`service.py:350` y luego `:428`). Crear `pre_route(question,
    history, state, bank_names)` que devuelva `"legacy"` o `None` **antes** de
    `decide()` para: respuestas «sí/no/ok/gracias», mención de bancos, y nombres
    de cuentas del catálogo bancario presentes en la pregunta junto a palabras
    de banco/efectivo/cuenta. Registrar `local_decision` en la traza.
    `route_is_consistent` se conserva como red de seguridad posterior.
  - **Pruebas:** con un enrutador simulado que cuenta llamadas, esas preguntas
    producen cero llamadas de enrutamiento y el mismo resultado final que antes.
  - **Resultado:** `policy.pre_route`; nombres de cuentas leídos una vez cada 10
    min (máx. 50, el límite del cliente) solo si la pregunta dice banco, efectivo,
    cuenta o caja. La traza conserva `modular_fallback = "route_inconsistent"`
    (lo exige una prueba existente; es el mismo veto, antes de clasificar) y
    añade `local_decision = "pre_route_legacy"`.

- [x] **T7. Resolver aclaraciones pendientes sin modelo**
  - **Archivos:** `agent/service.py`, `routing/state.py`; pruebas nuevas en
    `tests/unit/test_modular_context.py` y `test_list_selection.py`.
  - **Qué hacer:** si `state.pending_clarification == "cliente"`, hay
    `previous_route` de clientes con operación, existe lista abierta y el
    mensaje es un código de la lista o una posición válida (incluye T5),
    ejecutar `_consult_business` con esa operación, el código elegido y
    `codigo_exacto=True`, conservando período del estado. Si
    `pending_clarification == "periodo"` y el mensaje es exactamente un período
    relativo («este mes», «mes pasado», «esta semana», «semana pasada»), repetir
    la operación con ese período. Cualquier otra redacción sigue al flujo
    normal. Mismo patrón que `bank_report_followup`.
  - Para que funcione, guardar en el estado la operación pendiente cuando el
    servicio lista homónimos (`after_result` hoy solo guarda
    `pending_clarification`).
  - **Pruebas:** cero llamadas a modelos en ambos casos; un nombre nuevo, una
    pregunta distinta o una posición fuera de rango no usan el atajo.
  - **Resultado:** `pending_clarification_answer` en `service.py`;
    `ConversationState.pending_operation` y el período pedido se guardan cuando
    el servicio lista homónimos. Un `ToolHint` dentro del atajo vuelve al flujo
    normal. Pruebas en `test_modular_context.PendingClarificationTests`.

- [x] **T8. Conservar el estado al pasar por legacy**
  - **Archivos:** `agent/service.py` (hoy `:501-502` reinicia el estado),
    `routing/state.py`.
  - **Qué hacer:** tras una respuesta legacy con resultado de negocio
    verificado, guardar dominio, operación, entidad y período confirmados por
    el servicio (`answer.metadata`). No guardar nada que venga solo del modelo.
  - **Pruebas:** turno legacy seguido de turno modular conserva el cliente;
    cambio de dominio lo limpia; `/reiniciar` lo borra.
  - **Resultado:** solo con `[modular].enabled`; el turno legacy arranca limpio y
    solo un resultado con `entity` y `code` del servicio restaura
    `previous_route = clientes.<operación>`, identidad y período. T7 también
    quedó detrás de `[modular]`.

- [x] **T9. Reintento de extracción con el motivo del error**
  - **Archivos:** `agent/service.py` (bloque `:586-616`).
  - **Qué hacer:** cuando la extracción falla la validación, añadir a
    `messages` la llamada rechazada y un resultado de herramienta con el motivo
    (como hace `ToolHint`) antes del respaldo. Si `extraction_profile` es el
    mismo perfil que el activo, no crear un segundo proveedor ni contar un
    intento duplicado.
  - **Pruebas:** el respaldo recibe el motivo en sus mensajes; con perfiles
    iguales hay una sola instancia de proveedor.
  - **Resultado:** solo los rechazos del validador (textos fijos) pasan al
    respaldo; los errores de transporte no añaden mensajes. Con perfiles iguales
    `extraction_provider` es la misma instancia que el principal (las pruebas de
    integración de Jev local esperan que exista) y se cierra una sola vez. El
    presupuesto de dos llamadas de interpretación no cambia.

---

## Bloque D — Enrutamiento por niveles

- [x] **T10. Módulo de dominio con varias operaciones**
  - **Archivos:** `agent/context_builder.py`, `routing/registry.py`,
    `routing/contracts.py`; pruebas en `test_modular_context.py`.
  - **Qué hacer:** `select_context` acepta una lista de rutas del mismo
    dominio. El prompt incluye núcleo, dominio y el módulo de cada operación
    candidata; el enum de `operation` contiene exactamente esas operaciones;
    tablas y `topics` son la unión. Con una sola ruta el resultado es idéntico
    al actual (misma huella).

- [x] **T11. Decisión en tres niveles con las probabilidades de Jev**
  - **Archivos:** `agent/service.py` (`_select_modular`), `llm/config.py`
    (`ModularConfig`, `_modular`), `config/llm.modular.example.toml`,
    `docs/llm-configuration.md`; pruebas en `test_llm_config.py`,
    `test_modular_context.py`.
  - **Qué hacer:** nuevos campos opcionales `min_margin` y
    `min_domain_confidence`. Lógica:
    1. confianza ≥ `min_confidence` y margen ≥ `min_margin` → ruta de operación;
    2. si no, suma de probabilidades de un dominio ≥ `min_domain_confidence` →
       módulo de dominio (T10) con las rutas de ese dominio cuya probabilidad
       supere un piso, máximo 3;
    3. si no → legacy, **sin** llamar al clasificador mini.
    El clasificador mini queda como respaldo solo ante error HTTP o timeout de
    Jev. Sin los campos nuevos el comportamiento es el actual.
  - Registrar en la traza el nivel elegido (`operation`, `domain`, `legacy`).
  - **Pruebas:** tres distribuciones sintéticas, una por nivel; contador de
    llamadas de enrutamiento ≤ 1 salvo error de transporte.
  - **Resultado:** `tiered_decision` en `service.py` (piso 0.05, máx. 3 rutas);
    `route_is_consistent` filtra cada candidata. La traza guarda
    `routing_level` y `route_candidates`; el evaluador cuenta la ruta como
    correcta si la esperada está entre las candidatas.

- [x] **T12. Enrutador con estado e instrucciones reescritas**
  - **Archivos:** `routing/service.py`, `routing/jev.py`, `routing/registry.py`.
  - **Qué hacer:**
    - `routing_state` incluye `previous_route` y `pending_clarification` como datos.
    - Reescribir `INSTRUCTIONS` como regla general breve (qué se clasifica,
      prioridad de la pregunta actual, cuándo usar legacy). Mover los casos a
      las descripciones de cada ruta con «usar cuando» y «no usar cuando».
      **Eliminar el nombre propio** de las instrucciones; ese caso lo cubre T6.
    - En `ReferenceRouter`, enviar las instrucciones una sola vez (sistema), no
      también como descripción de la herramienta.
  - **Pruebas:** las instrucciones no contienen nombres de personas; el mensaje
    de sistema y la descripción de la herramienta no repiten el texto; los
    casos de desarrollo de enrutamiento existentes siguen pasando con
    enrutador simulado.

- [ ] **T13. Calibrar umbrales [API]**
  - Repetir T3 con T10–T12. Elegir `min_confidence`, `min_margin` y
    `min_domain_confidence` con las curvas de T2 sobre los conjuntos de
    desarrollo, y verificar una sola vez en `whatsapp-pilot.json`.
  - **Aceptar si:** acierto de ruta ≥ línea base y ≤ 1.1 llamadas de
    enrutamiento por turno. Documentar los valores como calibrados para este
    conjunto, no como garantía de producción.
  - **Pendiente (2026-11-12):** sin claves ni red. Comandos en
    `docs/evaluations/thresholds/PENDIENTE.md`.

---

## Bloque E — Módulos de prompt

- [x] **T14. Partir `core.md` y declarar dependencias por ruta**
  - **Archivos:** `agent/templates/core.md`, crear `templates/shared/identity.md`
    y `templates/shared/time.md`, `routing/contracts.py` (`Route.modules`),
    `routing/registry.py`, `agent/context_builder.py`,
    `scripts/context_inventory.py`, `docs/modular-context-inventory.json`.
  - **Qué hacer:** `core.md` conserva función y límites, decisión por turno y
    aclaraciones. Las «comprobaciones antes de llamar a consultar_negocio» y la
    sección de identidad pasan a `shared/identity.md`; la de tiempo a
    `shared/time.md`. Cada ruta declara sus módulos: clientes usan ambos;
    `gastos.exploracion` solo `shared/time.md`. No se pierde ninguna regla:
    solo se mueve texto.
  - **Pruebas:** la unión de módulos de una ruta de clientes contiene todas las
    líneas del `core.md` anterior; la ruta de gastos no contiene reglas de
    identidad de clientes.
  - **Resultado:** `Route.modules`; orden núcleo → compartidos → dominio →
    operación. La prueba existente que exigía «homónimos» en todas las rutas se
    ajustó, con aprobación del dueño, para exigirlo solo en rutas de personas.
    Copia del `core.md` anterior en `tests/unit/data/core_before_split.md`.

- [ ] **T15. Una sola fuente de verdad para `system.md`**
  - **Archivos:** crear `templates/domains/banks.md`, `domains/products.md`,
    `templates/examples.md` (con el contenido que hoy solo vive en
    `system.md`), crear `scripts/build_system_prompt.py`,
    `tests/unit/test_prompt_parity.py`, `agent/prompt_files.py` (`PROMPT_VERSION`).
  - **Qué hacer:** `system.md` pasa a generarse como composición ordenada de
    todos los módulos. El script lo regenera; la prueba falla si el archivo en
    disco difiere de la composición. Los prompts locales personalizados
    (`[prompts]`) siguen forzando el flujo anterior sin cambios.
  - El texto resultante puede diferir del actual: sube `PROMPT_VERSION` y
    documenta el cambio.
  - **Pruebas:** paridad; `load_prompt_bundle` sigue respetando el límite de
    26,000 caracteres.

- [ ] **T16. Prompt coherente con el enum y salida de ruta equivocada**
  - **Archivos:** `templates/domains/customers.md`, `agent/context_builder.py`,
    `agent/prompts.py`, `agent/service.py`.
  - **Qué hacer:**
    - El texto modular solo menciona las operaciones habilitadas en ese turno
      (la lista de operaciones se genera, no se escribe fija en el módulo).
    - En modo modular, añadir el motivo `otra_operacion` a `pedir_aclaracion`:
      el servicio lo trata como **un único** regreso al intérprete completo en
      el mismo turno, lo registra en la traza (`modular_fallback =
      "wrong_route"`) y nunca lo muestra al usuario. No se ofrece en legacy.
  - **Pruebas:** un modelo simulado que responde `otra_operacion` produce
    exactamente una llamada legacy posterior; dos seguidas terminan en
    aclaración de criterio, no en bucle.

- [ ] **T17. Ejemplos por operación**
  - **Archivos:** `templates/operations/*.md`.
  - **Qué hacer:** añadir a cada operación uno o dos ejemplos con identidades
    ficticias, empezando por las de mayor confusión en la matriz de T3. No
    copiar casos de `whatsapp-pilot.json`.
  - **Medir [API]:** acierto de parámetros dada la ruta ≥ antes de T17. Si un
    ejemplo empeora un caso, se retira.

---

## Bloque F — Costo: caché, historial y cobertura de dominios

- [ ] **T18. Ordenar el prompt de estable a variable**
  - **Archivos:** `agent/prompts.py` (`build_messages`), `agent/context_builder.py`,
    `agent/service.py` (hoy añade el estado al final del sistema, `:509-512`).
  - **Qué hacer:** orden del mensaje de sistema: núcleo → compartidos →
    dominio → esquema y contexto del dominio → operación. Calendario y estado
    conversacional van en un mensaje de sistema aparte, al final. Seleccionar
    `topics` por dominio en lugar de por operación para que el prefijo sea
    común entre operaciones del mismo dominio.
  - **Medir [API]:** `cached_input_tokens` y costo por consulta correcta antes y
    después, con `--repeat 3`. Adoptar solo si el costo baja sin perder acierto;
    publicar el resultado aunque sea negativo.

- [ ] **T19. Esquema de herramienta fijo por dominio (experimento) [API]**
  - **Qué hacer:** bandera `fixed_tool_schema`. Con ella, el enum de
    `operation` es el del dominio completo y la operación de la ruta se exige
    en `validate_tool_call`. Comparar con T18.
  - **Aceptar solo si** baja el costo por consulta correcta y no baja el
    acierto de parámetros. Si no, dejar la bandera apagada y documentarlo.

- [ ] **T20. Historial reducido con estado confirmado**
  - **Archivos:** `agent/service.py`, `agent/prompts.py`.
  - **Qué hacer:** bandera `compact_history`. En rutas modulares con entidad
    confirmada en el estado, enviar solo el último intercambio más el estado.
    En legacy, con lista abierta o con aclaración pendiente, se envía el
    historial completo como hoy.
  - **Pruebas:** continuaciones («¿y su último pago?», «¿y este mes?») siguen
    resolviendo con historial reducido; lista abierta mantiene el historial.

- [ ] **T21. Dominio modular de proveedores**
  - **Archivos:** `routing/registry.py`, `templates/domains/`, `templates/operations/movimientos.md`,
    `llm/config.py` (dominios válidos), `routing/policy.py`.
  - **Qué hacer:** rutas `proveedores.*` reutilizando el dominio de personas
    con `entidad=proveedor`, más `movimientos`. Mantener la regla de homónimos
    entre catálogos: ambas fuentes siguen disponibles.

- [ ] **T22. Dominio modular de bancos**
  - **Archivos:** como T21, con `templates/domains/banks.md` (de T15) y
    operaciones `saldo_banco`, `saldos_bancos`, `movimientos_banco`.
  - **Qué hacer:** rutas `bancos.*`; quitar de `policy.py` el veto que manda
    todo banco a legacy **solo cuando** el dominio esté habilitado. Conservar
    `bank_report_followup`.

- [ ] **T23. Dominio modular de productos**
  - **Qué hacer:** rutas `productos.existencias` y `productos.ventas`. El resto
    de inventario (precios, insumos, entradas) sigue como exploración acotada.

- [ ] **T24. Completar el conjunto de aceptación**
  - Añadir casos a `evals/cases/whatsapp-pilot.json` hasta equilibrar los
    cuatro dominios (hoy: 16 clientes, 8 bancos, 4 proveedores, 2 productos).
    Solo con redacciones reales nuevas que entregue el dueño; si no las hay,
    dejar la tarea pendiente y no inventar mensajes como si fueran reales.

---

## Cierre

- [ ] **T25. Evaluación final y documentación [API]**
  - Repetir T3 con todo activado. Tabla comparativa contra la línea base:
    acierto de ruta, acierto de parámetros, llamadas por turno, tokens, costo
    por consulta correcta, porcentaje de turnos en legacy (meta < 25%).
  - Actualizar `docs/modularization.md`, `docs/prompt-design.md`,
    `docs/llm-configuration.md`, `docs/business-queries.md` y `CHANGELOG.md`.
  - Informe final: qué se adoptó, qué se descartó con su medición, qué quedó
    pendiente por falta de claves o de datos.

## Orden y dependencias

```
T0 → T1 → T2 → T3
T4, T5            (independientes; pueden ir primero, corrigen errores reales)
T6 → T7 → T8 → T9
T10 → T11 → T12 → T13
T14 → T15 → T16 → T17
T18 → T19 → T20
T21, T22, T23     (requieren T10, T14 y T15)
T24               (requiere datos nuevos del dueño)
T25               (al final)
```

## Decisiones pendientes del dueño — no implementar sin respuesta

1. **Reporte de banco sin fechas:** hoy devuelve todo el historial, mientras el
   estado de cuenta de personas pide fechas. ¿Debe pedirlas también?
2. **«Saldo de Enrique» tras consultar su banco:** hoy lista clientes y
   proveedores. ¿Debe ofrecer también la cuenta bancaria homónima?
3. **Vencimiento de la memoria (30 minutos):** un seguimiento horas después
   pierde el cliente. ¿Se mantiene?

## Condiciones para detenerse y preguntar

- Una tarea exige cambiar una regla contable o el significado de un importe.
- Una prueba existente contradice lo pedido.
- Una medición muestra que una fase empeora el acierto y no hay forma de
  conservarlo sin ajustar reglas contra el conjunto de aceptación.
