# Plan: mejorar respuestas y optimizar el flujo modular con Jev

Fecha: 2026-11-12. Revisión de `main` tras el merge de `feat/modularization` (PR #21).
Continúa `2026-11-10-contexto-modular-jev-modelos.md`; no lo reemplaza.

## Objetivo

1. Que el agente responda bien más veces a la primera (menos aclaraciones
   innecesarias y menos rutas equivocadas).
2. Que cada mensaje use menos llamadas y menos tokens, medido por consulta
   correcta y no por llamada.

## Punto de partida medido

| Dato | Valor | Fuente |
| --- | --- | --- |
| Flujo anterior, una llamada | ~10,200 tokens | `docs/usage-live/benchmark.json` |
| Flujo anterior, «Hola» | 2 llamadas, 20,276 tokens | mismo archivo |
| Flujo anterior, gastos | 3 llamadas, 31,173 tokens | mismo archivo |
| Modular + clasificador mini vs. anterior | 16/16 vs. 15/16, costo +23.3% | `docs/modularization.md` |
| Jev + mini en el piloto | seleccionado; sin cifra propia de aciertos publicada | `evaluations/local-pilot-selection.json` |
| Umbral Jev | 0.8, sin calibrar | `config/llm.jev-local.toml` |

La modularización reduce 45–66% de caracteres por ruta, pero todavía no hay
ahorro total demostrado: el clasificador y los respaldos añaden llamadas.

## Hallazgos

### A. Enrutamiento

1. **Los vetos deterministas corren después de pagar el enrutador.**
   `route_is_consistent` (`routing/policy.py`) rechaza «sí/no/ok/gracias» y
   cualquier mención de bancos, pero `_select_modular` llama primero a Jev
   (`agent/service.py:350`) y veta después (`:428`). Esa llamada es desperdicio.
2. **Confianza baja = segunda clasificación.** Bajo el umbral se llama al
   clasificador mini (`service.py:389-423`). Peor caso antes de tocar datos:
   Jev + mini + extracción + respaldo de extracción.
3. **Las probabilidades de Jev no se aprovechan.** Solo se compara la confianza
   del primer lugar. Si los dos primeros son del mismo dominio
   (`clientes.pagos` vs. `clientes.total_pagos`), hoy se reclasifica o se cae
   al prompt completo, cuando bastaría el módulo del dominio con ambas
   operaciones.
4. **El enrutador no ve el estado.** `routing_state` envía solo el último
   intercambio (`routing/service.py`, `[-2:]`). `ConversationState` ya guarda
   `previous_route` y `pending_clarification`, pero no se le pasa.
5. **`INSTRUCTIONS` es una lista de parches.** Mezcla la regla general con
   casos puntuales (incluye el nombre de una persona real) y en el clasificador
   mini se envía dos veces: como mensaje de sistema y como descripción de la
   herramienta. Las descripciones por ruta (`registry.DESCRIPTIONS`) son de una
   línea y no llevan contraejemplos.
6. **`legacy` es demasiado frecuente y es la ruta más cara.** Bancos,
   inventario, ventas, proveedores explícitos, «sí» y la elección de un código
   van al prompt de ~10k tokens, además de haber pagado el enrutador. La
   elección de homónimos, que es el flujo más común en WhatsApp, siempre cae ahí.
7. **Al pasar por `legacy` se pierde el estado** (`service.py:501-502`), así
   que el siguiente turno empieza sin ruta previa.

### B. Modularización de prompts

1. **`core.md` no es núcleo.** De sus 6,810 caracteres, las comprobaciones a–f,
   identidad y tiempo son reglas de clientes y pagos. Se envían también a
   `gastos.exploracion`, donde no aplican.
2. **El prompt modular contradice la herramienta.** `core.md` ordena llamar
   `estado_cuenta` o `saldos_coincidencias` y `customers.md` enumera todas las
   operaciones, pero `select_context` deja el enum en una sola
   (`context_builder.py:55-60`). Si la ruta es incorrecta, el modelo no puede
   corregirla: solo le queda pedir una aclaración que no corresponde.
3. **No hay salida de una ruta equivocada.** En operaciones modulares hay como
   máximo dos intentos y ninguno puede volver al intérprete completo.
4. **Dos fuentes de verdad.** `system.md` (18,983 caracteres) repite a mano lo
   que está en `core.md`, `domains/` y `operations/`. Cada corrección debe
   hacerse dos veces y pueden divergir sin que una prueba lo detecte.
5. **El respaldo de extracción repite la misma llamada.** Con
   `extraction_profile = "openai"` (igual al perfil activo) y rutas de clientes,
   el respaldo (`service.py:603-614`) usa el mismo modelo, mismos mensajes,
   mismas herramientas y temperatura 0, sin informar el error de validación.
6. **El orden del prompt impide reutilizar caché.** El sistema se arma como
   núcleo → dominio → operación → esquema → contexto → calendario → estado, y el
   enum de la herramienta cambia por operación. Lo variable queda antes que lo
   estable. Los eventos de uso ya registran `cached_input_tokens`, pero no se
   ha revisado cuánto se aprovecha. Hipótesis por confirmar con esa medición.
7. **El historial no se reduce.** Se envían hasta 8 mensajes de 3,500
   caracteres aunque el estado ya resume cliente y período.

### C. Evaluación

1. Solo se evalúa la primera herramienta y sus parámetros, una ejecución por
   caso, con casos de desarrollo usados para ajustar reglas.
2. Los errores del enrutador y del extractor se reportan juntos. No hay matriz
   de confusión de Jev ni curva de calibración.
3. No hay métrica de respuesta final ni de costo por consulta correcta.

### D. Observaciones del piloto real (2 al 11 de noviembre)

Del chat exportado de WhatsApp: 48 preguntas de negocio (4 por voz). El export y sus
adjuntos no se guardan en el repo; los casos derivados están anonimizados en
`evals/cases/whatsapp-pilot.json` (30 casos).

1. **Respuesta incorrecta entregada.** «De cuánto fue la última nota del
   cliente X» se respondió con su último pago. Es el único caso donde el
   agente contestó otra cosa sin avisar.
2. **«Última compra / última nota» de un cliente es la petición sin soporte
   más repetida** (7 veces, con cuatro respuestas distintas: fuera de alcance,
   precisar concepto, «solo proveedores» y el pago equivocado). `ultima_nota`
   existe solo para proveedores. Requiere decisión de producto.
3. **Un número solo no elige de la lista.** Tras una lista numerada, «3» pidió
   cliente de nuevo; «El tercero» sí funcionó. `list_position` exige artículo.
4. **Códigos dictados por voz.** «A. Esparza tres» no se reconoció como el
   código con punto y dígito.
5. **«Dame un reporte de este mes» tras un saldo de banco** falló una vez
   (pidió precisar el concepto). Ya está cubierto por `bank_report_followup`.
6. **Reporte de banco sin fechas** devuelve todo el historial, mientras el
   estado de cuenta de personas pide fechas. Comportamiento inconsistente.
7. **«Saldo de Enrique» justo después de consultar su banco** listó clientes y
   proveedores, sin ofrecer la cuenta bancaria homónima.
8. **Seguimiento tras varias horas** pierde el cliente (la memoria vence a los
   30 minutos). Es el diseño actual; se anota por si el uso real lo contradice.
9. **Muchos `/diagnostico` sin respuesta** y un «Hola» sin contestar: es
   disponibilidad del webhook local, fuera del alcance de este plan.

Distribución real: clientes domina, bancos concentra las pruebas recientes,
proveedores y productos casi no aparecen. El conjunto final necesita más
mensajes reales de esos dos dominios para quedar equilibrado.

## Plan

Cada fase es independiente, reversible por bandera y termina con medición.
Orden por relación impacto/riesgo.

### Fase 0 — Medir antes de cambiar (requisito de todo lo demás)

- Ampliar `evaluation/modular.py` para reportar por separado: acierto de ruta,
  acierto de parámetros dada la ruta correcta, llamadas por turno, tokens
  (normales y en caché) y latencia p50/p95.
- Guardar probabilidades de Jev por caso y generar matriz de confusión y curva
  acierto/cobertura por umbral.
- Conjunto final separado (objetivo: 80–100 casos, con 3 repeticiones). Primer
  bloque listo: `evals/cases/whatsapp-pilot.json`, 30 casos con la redacción
  real del piloto e identidades ficticias (16 clientes, 4 proveedores, 8
  bancos, 2 productos). Falta completar proveedores y productos.
- Añadir casos de conversación completa con SQLite sintético que comprueben el
  importe final, no solo la primera herramienta.

Criterio de salida: línea base publicada con las cuatro métricas para
`legacy`, `reference` y `jev`.

### Fase 1 — Quitar llamadas que no aportan

1. **Pre-enrutador determinista.** Mover los vetos de `policy.py` a un paso
   previo a `decide()`: respuestas «sí/no/ok», mención de bancos y nombres de
   cuentas del catálogo bancario resuelven `legacy` sin llamar a Jev.
2. **Resolver aclaraciones pendientes sin modelo.** Si
   `pending_clarification == "cliente"` y el mensaje es un código o una
   posición de la lista abierta, repetir `previous_route` con ese código
   (`chosen_customer`/`listed_row` ya hacen la validación). Igual para
   `periodo` con un período relativo. Es el mismo patrón que
   `bank_report_followup`.
3. **Conservar el estado al pasar por `legacy`**, registrando dominio y
   entidad confirmada por el servicio.
4. **Aceptar un número solo como posición de la lista abierta** («3») y
   normalizar códigos dictados («A. Robles tres» → `A.ROBLES3`) antes de
   buscar; ambos fallaron en el piloto.
5. **Sustituir el respaldo idéntico de extracción** por un reintento que
   incluya el motivo de validación como resultado de herramienta; si el perfil
   de extracción es el activo, no duplicar la llamada.

Meta: −1 llamada en elecciones de lista, confirmaciones y preguntas de bancos;
ninguna regresión en el conjunto final.

### Fase 2 — Enrutamiento por niveles con las probabilidades de Jev

1. Decidir en tres niveles en lugar de umbral único:
   - margen alto en una ruta → módulo de operación;
   - masa de probabilidad concentrada en un dominio → módulo de dominio con las
     operaciones candidatas en el enum;
   - lo demás → intérprete completo, **sin** segunda clasificación.
2. Calibrar los umbrales con la curva de la Fase 0, en español y por dominio.
   Eliminar el respaldo al clasificador mini salvo error HTTP/timeout de Jev.
3. Pasar al enrutador `previous_route` y `pending_clarification` como datos.
4. Reescribir `INSTRUCTIONS` como regla general breve y mover los casos a las
   descripciones de cada ruta, con «usar cuando» y «no usar cuando». Quitar el
   nombre propio; ese caso lo cubre el pre-enrutador con el catálogo de cuentas.
   En el clasificador mini, enviar las instrucciones una sola vez.

Meta: acierto de ruta ≥ línea base con ≤ 1.1 llamadas de enrutamiento por turno.

### Fase 3 — Reestructurar los módulos de prompt

1. **Partir `core.md`** en `core` (función, seguridad, decisión por turno,
   aclaraciones), `shared/identity` y `shared/time`. Cada ruta declara sus
   dependencias en el registro; gastos deja de recibir reglas de clientes.
2. **Una sola fuente de verdad.** Generar `system.md` como composición de todos
   los módulos más los ejemplos, con una prueba que falle si difieren. Los
   prompts locales personalizados siguen forzando el flujo anterior.
3. **Hacer coherente el prompt con el enum.** El módulo de dominio solo debe
   mencionar las operaciones habilitadas en ese turno.
4. **Salida de ruta equivocada.** Añadir el motivo `otra_operacion` a
   `pedir_aclaracion` solo en modo modular: el servicio lo trata como un único
   regreso al intérprete completo, registrado en la traza, nunca como
   respuesta al usuario.
5. **Ejemplos por operación.** Mover a cada `operations/*.md` uno o dos de los
   ejemplos que hoy solo existen en `system.md`, empezando por las operaciones
   con más fallos en la matriz de confusión.

Meta: acierto de parámetros dada ruta correcta ≥ línea base; aclaraciones
`criterio` innecesarias a la baja; cero divergencias entre flujos.

### Fase 4 — Tokens: caché e historial

1. Reordenar el mensaje de sistema de estable a variable: núcleo → compartidos
   → dominio → esquema y contexto del dominio → operación → calendario → estado.
   Mover calendario y estado a un mensaje aparte al final.
2. Comparar con `cached_input_tokens` dos variantes: enum por operación
   (actual) frente a esquema de herramienta fijo por dominio con la operación
   validada en `validate_tool_call`. Adoptar la segunda solo si no baja el
   acierto.
3. En rutas modulares con estado confirmado, enviar el último intercambio más
   el estado en lugar de los 8 mensajes. Mantener el historial completo en
   `legacy`.

Meta: reducción medida de tokens facturados por consulta correcta; publicar el
resultado aunque sea negativo.

### Fase 5 — Reducir el uso de `legacy`

Clientes, proveedores, bancos y productos tienen la misma importancia. La
frecuencia reciente de bancos refleja pruebas de integración, no uso real, y
no se usa para priorizar. Los tres dominios pendientes se modularizan con el
mismo patrón de registro (ruta, dependencias, operaciones, herramientas):

- **Proveedores:** reutiliza el dominio de personas con `entidad=proveedor` y
  añade `ultima_nota` y `movimientos`. Es el de menor esfuerzo.
- **Bancos:** operaciones verificadas, catálogo de cuentas cerrado y
  continuaciones ya resueltas por código.
- **Productos:** existencias y ventas como operaciones; el resto de inventario
  sigue como exploración acotada.

El orden entre ellos lo decide el costo medido: tokens gastados en `legacy`
por dominio en la línea base de la Fase 0, de mayor a menor.

Meta: `legacy` por debajo del 25% de los turnos, con los cuatro dominios
cubiertos por igual en el conjunto de evaluación.

## Fuera de alcance

- Cambiar de modelo principal o activar nano: los candidatos económicos ya
  fallaron (14/32, 28/32, 24/32) y conviene reevaluarlos después de las fases
  2 y 3, cuando los errores del enrutador estén separados de los del extractor.
- Promover gastos a operación verificada: depende de confirmar reglas contables.
- Activar en producción: cada fase se valida en el piloto local.

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Atajos deterministas responden algo distinto a lo pedido | Solo patrones exactos; todo lo demás conserva el intérprete; casos de prueba negativos |
| Módulo de dominio amplía lo que el modelo puede llamar | El servicio sigue validando identidad, parámetros y permisos |
| Umbrales sobreajustados a pocos casos | Conjunto final separado y repeticiones; revisar con datos del piloto |
| Menos historial rompe referencias | Solo con estado confirmado por el servicio; bandera para revertir |
| El reordenamiento no mejora la caché | Se mide antes de adoptar; el cambio es de orden, no de contenido |

## Decisiones tomadas (2026-11-12)

1. El conjunto final de la Fase 0 usa preguntas reales anonimizadas del piloto
   de WhatsApp, repartidas por igual entre clientes, proveedores, bancos y
   productos.
2. La prioridad es el costo por consulta correcta, no la latencia. La Fase 4
   va antes que la 5 y toda fase se acepta o descarta con esa métrica.
3. Ningún dominio tiene prioridad sobre los otros; ver Fase 5.

4. «Última nota / última compra» debe existir para clientes. El saldo de un
   cliente es el balance entre sus notas de crédito y sus notas de cobranza;
   la última compra de un cliente es su última nota de crédito.

Las tareas ejecutables están en `2026-11-12-tareas-prompts-enrutamiento-jev.md`.
