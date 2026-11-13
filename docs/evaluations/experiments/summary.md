# Experimentos T17–T19 — 2026-11-12

Las tres variantes usan la config de T13 (`docs/evaluations/thresholds/llm.t13.toml`: 5 dominios,
`min_confidence = 0.8`, `min_margin = 0.2`, `min_domain_confidence = 0.4`, `compact_history = true`)
y cambian solo una bandera. Se ejecutaron sobre los conjuntos de **desarrollo**
(`modular-development.json`, `intent-routing.json` y `bank-routing.json`) con `--repeat 3`: 252
turnos por variante, las tres `measured`.

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/modular-development.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/experiments/<v> --repeat 3 --variants custom --config docs/evaluations/experiments/llm.<v>.toml
```

con `<v>` = `base`, `stable_prefix` (T18) y `fixed_tool_schema` (T19). Costo estimado: base $0.252,
stable_prefix $0.307, fixed_tool_schema $0.248 (todos completos, sin uso desconocido).

## Resultado global

| variante | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| (a) base | 252 | 221 (87.7 %) | 84.2 % (n=183) | 90.4 % (n=218) | 1.88 | 0.95 | 8494 / 371 / 6039 | $0.00114 | 26.6 % | 0.88 s | 1.16 s |
| (b) stable_prefix = true (T18) | 252 | 227 (90.1 %) | 84.2 % (n=183) | 91.3 % (n=219) | 1.88 | 0.95 | 9434 / 371 / 6557 | $0.00135 | 27.0 % | 0.84 s | 1.08 s |
| (c) fixed_tool_schema = true (T19) | 252 | 218 (86.5 %) | 85.8 % (n=183) | 89.4 % (n=218) | 1.88 | 0.95 | 8424 / 371 / 6004 | $0.00114 | 25.4 % | 0.86 s | 1.12 s |

Escala del ruido: un caso que cambia en las 3 repeticiones mueve el acierto total 1.2 puntos
(3/252). Diferencias de 1–2 puntos no son concluyentes con esta muestra.

## T17 — ejemplos por operación

**Limitación:** los ejemplos de T17 ya están en el código, así que la línea base del paso 2
(`docs/evaluations/pilot-baseline/`) también los incluye en las variantes modulares. Por eso esta
comparación **no aísla el efecto de T17**: la línea base y la variante (a) difieren además en los
dominios activos, los umbrales y `compact_history`. Medir el efecto real exigiría ejecutar el
código anterior al commit de T17; no se hizo.

Comparación sobre los conjuntos que comparten ambas corridas (`intent-routing.json` y
`bank-routing.json`, 68 casos × 3 = 204 turnos):

| variante (intent + bank) | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| línea base legacy_mini | 204 | 184 (90.2 %) | — (n=0) | 90.2 % (n=204) | 0.99 | 0.00 | 13308 / 26 / 12978 | $0.00163 | 100.0 % | 0.73 s | 1.00 s |
| línea base reference_mini | 204 | 151 (74.0 %) | 81.4 % (n=156) | 84.5 % (n=161) | 1.90 | 0.94 | 8388 / 40 / 7110 | $0.00174 | 34.3 % | 1.41 s | 1.69 s |
| línea base jev_mini | 204 | 162 (79.4 %) | 87.8 % (n=156) | 86.0 % (n=171) | 2.21 | 1.25 | 9328 / 173 / 7598 | $0.00143 | 38.7 % | 0.92 s | 1.72 s |
| (a) base | 204 | 174 (85.3 %) | 81.4 % (n=156) | 88.5 % (n=182) | 1.91 | 0.96 | 8692 / 373 / 6153 | $0.00122 | 28.4 % | 0.88 s | 1.09 s |
| (b) stable_prefix | 204 | 179 (87.7 %) | 82.1 % (n=156) | 89.6 % (n=183) | 1.91 | 0.96 | 9559 / 373 / 6714 | $0.00139 | 28.4 % | 0.86 s | 1.06 s |
| (c) fixed_tool_schema | 204 | 173 (84.8 %) | 83.3 % (n=156) | 87.4 % (n=182) | 1.91 | 0.96 | 8598 / 373 / 6077 | $0.00121 | 27.0 % | 0.86 s | 1.11 s |

`arguments_accuracy_given_route`: (a) base **88.5 %**, frente a 86.0 % de `jev_mini` y 84.5 % de
`reference_mini` en la línea base. **No baja.**

Casos que empeoraron frente a `jev_mini` de la línea base (aprobados de 3, en intent + bank).
Como pide la tarea, solo se reportan: no se retiró ningún ejemplo.

| variante | caso | jev_mini (línea base) | reference_mini (línea base) | variante | ruta elegida | herramienta / operación obtenida |
|---|---|---|---|---|---|---|
| (a) base | `global-customer-count` | 3/3 | 2/3 | 2/3 | legacy | `pedir_aclaracion` |
| (a) base | `homonym-balances` | 3/3 | 3/3 | 1/3 | `clientes.saldos_coincidencias` | `consultar_negocio` / `saldos_coincidencias` |
| (a) base | `last-payment-keeps-chosen-code` | 3/3 | 2/3 | 1/3 | `clientes.ultimo_pago` | `consultar_negocio` / `ultimo_pago` |
| (b) stable_prefix | `last-payment-keeps-chosen-code` | 3/3 | 2/3 | 2/3 | `clientes.ultimo_pago` | `consultar_negocio` / `ultimo_pago` |
| (c) fixed_tool_schema | `homonym-balances` | 3/3 | 3/3 | 2/3 | `clientes.saldos_coincidencias` | `pedir_aclaracion` |
| (c) fixed_tool_schema | `last-payment-keeps-chosen-code` | 3/3 | 2/3 | 1/3 | `clientes.ultimo_pago` | `consultar_negocio` / `ultimo_pago` |
| (c) fixed_tool_schema | `typo-in-question` | 3/3 | 3/3 | 1/3 | `clientes.saldo` | `consultar_negocio` / `saldo` |

Las operaciones implicadas son `saldos_coincidencias` (homónimos), `ultimo_pago` (código elegido
de la lista) y `saldo` (pregunta con errata); `global-customer-count` es una aclaración. En
`homonym-balances` y `last-payment-keeps-chosen-code` la ruta y la operación son correctas: el
fallo está en los parámetros. Que un ejemplo concreto sea la causa no se puede afirmar por la
limitación anterior. Queda a decisión del dueño revisar los ejemplos de `saldos_coincidencias` y
`ultimo_pago`.

**T17: sin cerrar.** El criterio global se cumple, pero no se midió antes y después de T17 ni se
decidió qué ejemplos retirar.

## T18 — `stable_prefix`

| métrica | (a) base | (b) stable_prefix | cambio |
|---|---|---|---|
| tokens de entrada por turno | 8494 | 9434 | +940 (+11 %) |
| tokens en caché por turno | 6039 | 6557 | +518 |
| tokens sin caché por turno | 2455 | 2877 | +422 (+17 %) |
| costo total de la corrida | $0.2518 | $0.3073 | +22 % |
| costo por consulta correcta | $0.001140 | $0.001354 | **+18.8 %** |
| acierto total | 87.7 % | 90.1 % | +2.4 pts (6 de 252) |
| arguments_acc_given_route | 90.4 % | 91.3 % | +0.9 pts |

La caché sube, pero el prompt crece más (los `topics` se eligen por dominio y no por operación),
así que el costo por consulta correcta **sube**. **No se adopta** (criterio: adoptar solo si baja
el costo sin perder acierto). La mejora de acierto (+2.4 pts) está en el límite del ruido; se
publica como resultado negativo en costo y posible señal de acierto, que habría que medir aparte.

## T19 — `fixed_tool_schema`

| métrica | (a) base | (c) fixed_tool_schema | cambio |
|---|---|---|---|
| tokens de entrada por turno | 8494 | 8424 | −70 |
| tokens en caché por turno | 6039 | 6004 | −35 |
| costo por consulta correcta | $0.001140 | $0.001136 | −0.4 % (ruido) |
| acierto total | 87.7 % | 86.5 % | −1.2 pts |
| arguments_acc_given_route | 90.4 % | 89.4 % | **−1.0 pts** |
| route_accuracy | 84.2 % | 85.8 % | +1.6 pts |

El esquema fijo no mejora la caché (los tokens en caché bajan levemente), el costo no cambia de
forma apreciable y el acierto de parámetros baja. **No se adopta**; la bandera queda apagada.
Resultado negativo.

## Por dominio

### (a) base

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 69 | 62 (89.9 %) | — (n=0) | 91.1 % (n=45) | 1.78 | 0.87 | 11470 / 333 / 9470 | $0.00134 | 65.2 % | 0.86 s | 1.36 s |
| bancos | 33 | 31 (93.9 %) | 63.6 % (n=33) | 96.9 % (n=32) | 2.00 | 1.00 | 8672 / 393 / 6532 | $0.00096 | 33.3 % | 0.88 s | 1.09 s |
| clientes | 108 | 92 (85.2 %) | 85.2 % (n=108) | 89.9 % (n=99) | 1.94 | 0.97 | 7561 / 383 / 4939 | $0.00111 | 6.5 % | 0.92 s | 1.19 s |
| compras | 3 | 3 (100.0 %) | 100.0 % (n=3) | 100.0 % (n=3) | 2.00 | 1.00 | 15313 / 383 / 13312 | $0.00152 | 100.0 % | 0.88 s | 1.01 s |
| gastos | 12 | 6 (50.0 %) | 100.0 % (n=12) | 50.0 % (n=12) | 1.25 | 1.00 | 2662 / 370 / 779 | $0.00037 | 0.0 % | 0.20 s | 0.81 s |
| productos | 15 | 15 (100.0 %) | 100.0 % (n=15) | 100.0 % (n=15) | 2.00 | 1.00 | 5162 / 391 / 2244 | $0.00078 | 0.0 % | 0.86 s | 1.05 s |
| proveedores | 12 | 12 (100.0 %) | 91.7 % (n=12) | 100.0 % (n=12) | 2.00 | 1.00 | 7575 / 395 / 3040 | $0.00151 | 8.3 % | 0.88 s | 1.06 s |

**custom** — 14 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 1/3 | bancos.saldo_banco → bancos.saldo_banco | pedir_aclaracion |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 1/3 | bancos.movimientos_banco → gastos.exploracion | describir_tablas |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| global-customer-count | intent-routing.json | aclaracion | 1/3 | None → legacy | consultar_negocio / contar_coincidencias |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| homonym-balances | intent-routing.json | clientes | 2/3 | clientes.saldos_coincidencias → clientes.saldos_coincidencias | pedir_aclaracion |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → legacy | consultar_negocio / ranking_deuda |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 2/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| modular-dev-rank-is-not-selection | modular-development.json | aclaracion | 1/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 2/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → proveedores.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → proveedores.ultima_nota | consultar_negocio / ultima_nota |  |

### (b) stable_prefix

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 69 | 66 (95.7 %) | — (n=0) | 93.3 % (n=45) | 1.78 | 0.87 | 12022 / 333 / 9939 | $0.00134 | 65.2 % | 0.80 s | 1.00 s |
| bancos | 33 | 30 (90.9 %) | 63.6 % (n=33) | 90.9 % (n=33) | 2.00 | 1.00 | 9430 / 392 / 6004 | $0.00150 | 36.4 % | 0.88 s | 1.09 s |
| clientes | 108 | 96 (88.9 %) | 84.3 % (n=108) | 93.9 % (n=99) | 1.94 | 0.97 | 9169 / 383 / 6167 | $0.00137 | 7.4 % | 0.88 s | 1.14 s |
| compras | 3 | 3 (100.0 %) | 100.0 % (n=3) | 100.0 % (n=3) | 2.00 | 1.00 | 15313 / 383 / 13312 | $0.00152 | 100.0 % | 0.91 s | 1.00 s |
| gastos | 12 | 6 (50.0 %) | 100.0 % (n=12) | 50.0 % (n=12) | 1.25 | 1.00 | 2662 / 370 / 800 | $0.00035 | 0.0 % | 0.20 s | 0.77 s |
| productos | 15 | 15 (100.0 %) | 100.0 % (n=15) | 100.0 % (n=15) | 2.00 | 1.00 | 5613 / 391 / 973 | $0.00135 | 0.0 % | 0.95 s | 1.30 s |
| proveedores | 12 | 11 (91.7 %) | 100.0 % (n=12) | 91.7 % (n=12) | 2.00 | 1.00 | 7035 / 396 / 3200 | $0.00136 | 0.0 % | 0.91 s | 1.06 s |

**custom** — 10 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 3/3 | bancos.movimientos_banco → bancos.movimientos_banco | pedir_aclaracion |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 2/3 | clientes.saldo → legacy | consultar_negocio / ranking_deuda |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 1/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → proveedores.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → proveedores.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 1/3 | proveedores.movimientos → proveedores.movimientos | consultar_negocio / movimientos |  |

### (c) fixed_tool_schema

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 69 | 62 (89.9 %) | — (n=0) | 93.3 % (n=45) | 1.78 | 0.87 | 11488 / 333 / 9481 | $0.00135 | 65.2 % | 0.83 s | 1.11 s |
| bancos | 33 | 32 (97.0 %) | 66.7 % (n=33) | 100.0 % (n=32) | 2.00 | 1.00 | 8365 / 393 / 5849 | $0.00101 | 30.3 % | 0.86 s | 1.06 s |
| clientes | 108 | 91 (84.3 %) | 86.1 % (n=108) | 88.9 % (n=99) | 1.94 | 0.97 | 7554 / 383 / 4965 | $0.00111 | 5.6 % | 0.89 s | 1.17 s |
| compras | 3 | 3 (100.0 %) | 100.0 % (n=3) | 100.0 % (n=3) | 2.00 | 1.00 | 15313 / 383 / 13312 | $0.00152 | 100.0 % | 0.84 s | 0.89 s |
| gastos | 12 | 6 (50.0 %) | 100.0 % (n=12) | 50.0 % (n=12) | 1.25 | 1.00 | 2662 / 370 / 800 | $0.00035 | 0.0 % | 0.17 s | 0.83 s |
| productos | 15 | 15 (100.0 %) | 100.0 % (n=15) | 100.0 % (n=15) | 2.00 | 1.00 | 5166 / 391 / 2620 | $0.00067 | 0.0 % | 0.86 s | 1.08 s |
| proveedores | 12 | 9 (75.0 %) | 100.0 % (n=12) | 75.0 % (n=12) | 2.00 | 1.00 | 6920 / 397 / 3392 | $0.00153 | 0.0 % | 0.88 s | 1.14 s |

**custom** — 14 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 1/3 | bancos.movimientos_banco → gastos.exploracion | describir_tablas |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| homonym-balances | intent-routing.json | clientes | 1/3 | clientes.saldos_coincidencias → clientes.saldos_coincidencias | pedir_aclaracion |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → legacy | consultar_negocio / ranking_deuda |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 2/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| modular-dev-rank-is-not-selection | modular-development.json | aclaracion | 3/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 1/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → proveedores.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → proveedores.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | proveedores.movimientos → proveedores.movimientos | consultar_negocio / movimientos |  |
| typo-in-question | intent-routing.json | clientes | 2/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
