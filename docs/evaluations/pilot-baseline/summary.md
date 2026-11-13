# Línea base del piloto (T3) — 2026-11-12

Medido con API (OpenAI `gpt-4.1-mini`, TypeSafe `jev-1.14.0`), `--repeat 3`, sobre
`whatsapp-pilot.json` (30 casos), `intent-routing.json` (55) y `bank-routing.json` (13): 98 casos ×
3 = 294 turnos por variante. Las tres variantes quedaron `measured`, ninguna `unavailable`.
Archivos: `benchmark.json`, `benchmark.html` y `calibration.json` (`jev_mini`).

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/pilot-baseline --repeat 3 --variants legacy_mini reference_mini jev_mini
python -m dbagg.evaluation.calibration --input docs/evaluations/pilot-baseline/benchmark.json --variant jev_mini --output docs/evaluations/pilot-baseline/calibration.json
```

Alcance: solo se puntúa la primera herramienta y sus parámetros (sin base de datos ni exactitud de
la respuesta). Los costos se estiman con la tarifa pública de `evaluation/cost.py` (2026-11-10);
todos los costos de esta corrida son completos (sin uso desconocido). Costo total estimado de la
corrida: **$1.18** (legacy $0.455, reference $0.398, jev $0.329).

Lectura de columnas: `route_accuracy` solo existe con `[modular]` activo (en `legacy_mini` no hay
rutas, n=0). `arguments_acc_given_route` cuenta los turnos con ruta correcta o en legacy.
`tokens/turno` es entrada / salida / entrada en caché. Latencias de extremo a extremo de la primera
decisión, incluidas las llamadas de enrutamiento.

## Por variante (los tres conjuntos)

| variante | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| legacy_mini | 294 | 252 (85.7 %) | — (n=0) | 85.7 % (n=294) | 1.00 | 0.00 | 13513 / 27 / 13005 | $0.00180 | 100.0 % | 0.73 s | 1.05 s |
| reference_mini | 294 | 203 (69.0 %) | 76.7 % (n=240) | 81.9 % (n=226) | 1.89 | 0.91 | 8475 / 41 / 7006 | $0.00196 | 33.7 % | 1.41 s | 1.83 s |
| jev_mini | 294 | 214 (72.8 %) | 82.1 % (n=240) | 82.7 % (n=237) | 2.17 | 1.21 | 9095 / 169 / 7370 | $0.00154 | 36.1 % | 0.91 s | 1.88 s |

## Solo el conjunto de aceptación (`whatsapp-pilot.json`)

Referencia para el criterio de T13 (acierto de ruta ≥ línea base).

| variante (solo whatsapp-pilot.json) | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| legacy_mini | 90 | 68 (75.6 %) | — (n=0) | 75.6 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13067 | $0.00227 | 100.0 % | 0.73 s | 1.49 s |
| reference_mini | 90 | 52 (57.8 %) | 67.9 % (n=84) | 75.4 % (n=65) | 1.89 | 0.83 | 8673 / 44 / 6770 | $0.00261 | 32.2 % | 1.38 s | 2.09 s |
| jev_mini | 90 | 52 (57.8 %) | 71.4 % (n=84) | 74.2 % (n=66) | 2.08 | 1.10 | 8565 / 159 / 6855 | $0.00188 | 30.0 % | 0.89 s | 2.27 s |

## Por dominio

**legacy_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 45 (93.8 %) | — (n=0) | 93.8 % (n=48) | 0.94 | 0.00 | 12672 / 18 / 12483 | $0.00144 | 100.0 % | 0.66 s | 0.95 s |
| bancos | 57 | 53 (93.0 %) | — (n=0) | 93.0 % (n=57) | 1.05 | 0.00 | 14220 / 27 / 13660 | $0.00176 | 100.0 % | 0.72 s | 1.58 s |
| clientes | 135 | 121 (89.6 %) | — (n=0) | 89.6 % (n=135) | 1.00 | 0.00 | 13517 / 30 / 12990 | $0.00174 | 100.0 % | 0.75 s | 1.03 s |
| compras | 3 | 3 (100.0 %) | — (n=0) | 100.0 % (n=3) | 1.00 | 0.00 | 13495 / 22 / 13312 | $0.00144 | 100.0 % | 0.66 s | 0.70 s |
| gastos | 6 | 3 (50.0 %) | — (n=0) | 50.0 % (n=6) | 1.00 | 0.00 | 13494 / 22 / 13312 | $0.00288 | 100.0 % | 0.69 s | 1.16 s |
| productos | 21 | 15 (71.4 %) | — (n=0) | 71.4 % (n=21) | 1.00 | 0.00 | 13498 / 25 / 13312 | $0.00202 | 100.0 % | 0.70 s | 0.94 s |
| proveedores | 24 | 12 (50.0 %) | — (n=0) | 50.0 % (n=24) | 1.00 | 0.00 | 13507 / 31 / 12197 | $0.00359 | 100.0 % | 0.81 s | 1.06 s |

**reference_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 38 (79.2 %) | — (n=0) | 85.2 % (n=27) | 1.81 | 0.88 | 9742 / 32 / 8603 | $0.00173 | 56.2 % | 1.41 s | 1.62 s |
| bancos | 57 | 38 (66.7 %) | 68.4 % (n=57) | 97.4 % (n=39) | 1.74 | 0.68 | 10790 / 37 / 9811 | $0.00215 | 63.2 % | 1.30 s | 2.11 s |
| clientes | 135 | 101 (74.8 %) | 81.1 % (n=132) | 85.2 % (n=115) | 1.99 | 0.98 | 6667 / 47 / 4768 | $0.00175 | 5.9 % | 1.42 s | 1.73 s |
| compras | 3 | 0 (0.0 %) | 0.0 % (n=3) | — (n=0) | 2.00 | 1.00 | 4133 / 40 / 2048 | — | 0.0 % | 1.33 s | 1.69 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 2469 / 28 / 1600 | — | 0.0 % | 0.89 s | 1.38 s |
| productos | 21 | 12 (57.1 %) | 85.7 % (n=21) | 66.7 % (n=18) | 2.00 | 1.00 | 12857 / 39 / 11806 | $0.00291 | 85.7 % | 1.38 s | 1.64 s |
| proveedores | 24 | 14 (58.3 %) | 66.7 % (n=21) | 66.7 % (n=21) | 1.92 | 1.00 | 8824 / 45 / 7504 | $0.00231 | 41.7 % | 1.42 s | 1.89 s |

**jev_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 40 (83.3 %) | — (n=0) | 89.3 % (n=28) | 2.23 | 1.29 | 10490 / 158 / 8893 | $0.00147 | 58.3 % | 0.97 s | 1.62 s |
| bancos | 57 | 45 (78.9 %) | 78.9 % (n=57) | 100.0 % (n=45) | 1.96 | 0.91 | 12162 / 134 / 10990 | $0.00171 | 73.7 % | 0.86 s | 2.28 s |
| clientes | 135 | 100 (74.1 %) | 83.3 % (n=132) | 83.6 % (n=116) | 2.28 | 1.32 | 6881 / 184 / 4928 | $0.00127 | 4.4 % | 0.91 s | 1.78 s |
| compras | 3 | 0 (0.0 %) | 0.0 % (n=3) | — (n=0) | 3.00 | 2.00 | 5210 / 189 / 1067 | — | 0.0 % | 1.62 s | 1.72 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 2736 / 164 / 1557 | — | 0.0 % | 0.17 s | 0.95 s |
| productos | 21 | 15 (71.4 %) | 100.0 % (n=21) | 71.4 % (n=21) | 2.00 | 1.00 | 14580 / 175 / 12672 | $0.00236 | 100.0 % | 0.86 s | 1.03 s |
| proveedores | 24 | 14 (58.3 %) | 71.4 % (n=21) | 66.7 % (n=21) | 2.12 | 1.25 | 8747 / 182 / 7072 | $0.00177 | 37.5 % | 0.97 s | 1.80 s |

**legacy_mini** — 16 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | legacy → legacy | pedir_aclaracion |  |

## Calibración de Jev (`jev_mini`, dominios por defecto `clientes` y `gastos`)

219 decisiones con probabilidades; acierto de ruta de Jev 81.3 %, acierto de dominio 86.3 %. Curvas y
matriz de confusión en `calibration.json`. Los umbrales **no** se eligieron aquí: el piloto es el
conjunto de aceptación (ver `docs/evaluations/thresholds/summary.md`).

## Casos fallidos por variante (`results[].passed == false`)

«Ruta esperada → elegida» muestra la ruta del registro; en `legacy_mini` todo es `legacy` porque
no hay rutas. `None` = caso de aclaración, que no se puntúa para ruta. La última columna es la
herramienta y la operación que devolvió el modelo (los parámetros completos están en
`benchmark.json`). Los casos son ficticios.
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 2/3 | legacy → legacy | consultar_negocio / ranking_deuda |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 1/3 | legacy → legacy | — |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | legacy → legacy | consultar_negocio / saldo |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| pilot-report-of-person-this-month | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | pedir_aclaracion |  |
| pilot-stock-how-much-of | whatsapp-pilot.json | productos | 3/3 | legacy → legacy | consultar_negocio / existencias |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / saldo |  |
| pilot-supplier-we-owe | whatsapp-pilot.json | proveedores | 1/3 | legacy → legacy | consultar_negocio / saldo |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |
| supplier-ranking | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / ranking_deuda |  |

**reference_mini** — 37 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 2/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-two-words | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-how-much-money | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | describir_tablas |  |
| chosen-code-is-exact | intent-routing.json | clientes | 1/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| global-customer-count | intent-routing.json | aclaracion | 1/3 | None → legacy | consultar_negocio / contar_coincidencias |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 1/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| pilot-balance-imperative | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 3/3 | legacy → gastos.exploracion | — |  |
| pilot-bank-report-without-period | whatsapp-pilot.json | bancos | 1/3 | legacy → legacy | consultar_negocio / estado_cuenta |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| pilot-bare-number-selection | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-code-reply-lowercase | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-ordinal-after-numbered-list | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-ordinal-keeps-pending-operation | whatsapp-pilot.json | clientes | 1/3 | clientes.ultimo_pago → clientes.saldos_coincidencias | — |  |
| pilot-statement-range-without-year | whatsapp-pilot.json | clientes | 1/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-stock-how-much-of | whatsapp-pilot.json | productos | 3/3 | legacy → legacy | consultar_negocio / existencias |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 2/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| purchases-stay-exploratory | intent-routing.json | compras | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 2/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| ranking-over-limit | intent-routing.json | aclaracion | 1/3 | None → clientes.ranking_deuda | consultar_negocio / ranking_deuda |  |
| sales-by-customer | intent-routing.json | aclaracion | 3/3 | None → clientes.total_pagos | pedir_aclaracion |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| sales-this-month | intent-routing.json | productos | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |

**jev_mini** — 29 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | describir_tablas |  |
| chosen-code-is-exact | intent-routing.json | clientes | 1/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-balance-imperative | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 3/3 | legacy → gastos.exploracion | — |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| pilot-bare-number-selection | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-code-reply-lowercase | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-ordinal-after-numbered-list | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-statement-range-without-year | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-stock-how-much-of | whatsapp-pilot.json | productos | 3/3 | legacy → legacy | consultar_negocio / existencias |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 1/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| purchases-stay-exploratory | intent-routing.json | compras | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 2/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| sales-by-customer | intent-routing.json | aclaracion | 3/3 | None → clientes.total_pagos | pedir_aclaracion |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |

## Observaciones (sin cambios de código)

- `legacy_mini` acierta más que las variantes modulares (85.7 % frente a 69.0 % y 72.8 %) y a un
  costo por consulta correcta parecido ($0.00180 frente a $0.00196 y $0.00154). Su prompt completo
  se sirve casi entero desde la caché de OpenAI (13 005 de 13 513 tokens de entrada).
- El fallo modular más repetido es elegir `clientes.saldos_coincidencias` cuando se espera
  `clientes.saldo` tras una selección de lista o un código (`pilot-bare-number-selection`,
  `pilot-code-reply-lowercase`, `pilot-ordinal-after-numbered-list`, `pilot-balance-imperative`,
  `pilot-supplier-first-of-list`, `instruction-inside-question`).
- Con los dominios por defecto, las preguntas de bancos terminan en `clientes.saldo` o en
  `gastos.exploracion` en vez de en legacy (`bank-balance-account-of`, `bank-withdrawals-last-week`,
  `pilot-bank-two-words-no-keyword`, `pilot-bank-report-followup-typo`).
- Los casos de `ultima_nota` (`pilot-last-note-is-not-last-payment`,
  `pilot-last-purchase-note-by-name`, `supplier-last-note-followup`) fallan en las tres variantes con
  la operación correcta: el fallo está en los parámetros, no en la ruta.
- `jev_mini` hace 1.21 llamadas de enrutamiento por turno: sin `min_margin` ni
  `min_domain_confidence`, cuando Jev no supera 0.8 se consulta también el clasificador mini.
