# Evaluación final (T25) — 2026-11-12

Medido con API (OpenAI `gpt-4.1-mini`, TypeSafe `jev-1.14.0`), `--repeat 3`, sobre
`whatsapp-pilot.json`, `intent-routing.json` y `bank-routing.json`: 294 turnos por variante, las
cuatro `measured`, ninguna `unavailable`. `custom` es `llm.all.toml`, «todo activado»:

- 5 dominios (`clientes`, `gastos`, `proveedores`, `bancos`, `productos`), enrutador Jev;
- umbrales de T13: `min_confidence = 0.8`, `min_margin = 0.2`, `min_domain_confidence = 0.4`;
- `compact_history = true`; `stable_prefix = false` y `fixed_tool_schema = false` (T18 y T19 no se
  adoptaron, ver `docs/evaluations/experiments/summary.md`).

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/final --repeat 3 --variants legacy_mini reference_mini jev_mini custom --config docs/evaluations/final/llm.all.toml
python -m dbagg.evaluation.calibration --input docs/evaluations/final/benchmark.json --variant custom --output docs/evaluations/final/calibration.json
```

Costo estimado de la corrida: $1.38 (legacy $0.432, reference $0.360, jev $0.318, custom
$0.267). Sin uso desconocido.

## Comparación con la línea base (los tres conjuntos)

«Línea base» = `docs/evaluations/pilot-baseline/` (T3). Las variantes de referencia se volvieron
a medir en esta corrida: la diferencia entre ambas mediciones (legacy 85.7 % → 84.0 %,
`jev_mini` 72.8 % → 70.1 %) da una idea del ruido entre corridas, de unos 2–3 puntos.

| variante | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| línea base · legacy_mini | 294 | 252 (85.7 %) | — (n=0) | 85.7 % (n=294) | 1.00 | 0.00 | 13513 / 27 / 13005 | $0.00180 | 100.0 % | 0.73 s | 1.05 s |
| línea base · reference_mini | 294 | 203 (69.0 %) | 76.7 % (n=240) | 81.9 % (n=226) | 1.89 | 0.91 | 8475 / 41 / 7006 | $0.00196 | 33.7 % | 1.41 s | 1.83 s |
| línea base · jev_mini | 294 | 214 (72.8 %) | 82.1 % (n=240) | 82.7 % (n=237) | 2.17 | 1.21 | 9095 / 169 / 7370 | $0.00154 | 36.1 % | 0.91 s | 1.88 s |
| T25 · legacy_mini | 294 | 247 (84.0 %) | — (n=0) | 84.0 % (n=294) | 1.00 | 0.00 | 13513 / 27 / 13263 | $0.00175 | 100.0 % | 0.73 s | 1.03 s |
| T25 · reference_mini | 294 | 200 (68.0 %) | 75.8 % (n=240) | 81.1 % (n=222) | 1.89 | 0.91 | 8380 / 41 / 7316 | $0.00180 | 32.7 % | 1.38 s | 1.74 s |
| T25 · jev_mini | 294 | 206 (70.1 %) | 81.2 % (n=240) | 80.4 % (n=235) | 2.16 | 1.20 | 9074 / 169 / 7461 | $0.00155 | 36.1 % | 0.89 s | 1.58 s |
| **T25 · custom (todo activado)** | 294 | 242 (82.3 %) | 79.2 % (n=240) | 85.7 % (n=265) | 1.92 | 0.97 | 8514 / 378 / 6334 | $0.00110 | 26.5 % | 0.89 s | 1.14 s |

| métrica | línea base legacy_mini | línea base jev_mini | **T25 custom** | meta / lectura |
|---|---|---|---|---|
| acierto total | 85.7 % | 72.8 % | **82.3 %** | +9.5 pts frente a jev; −3.4 frente a legacy (−1.7 frente a legacy en esta corrida) |
| route_accuracy | — | 82.1 % | **79.2 %** | no comparable de forma directa: custom puntúa contra rutas de 5 dominios |
| arguments_accuracy_given_route | 85.7 % | 82.7 % | **85.7 %** | igual a legacy; +3.0 frente a jev |
| llamadas por turno | 1.00 | 2.17 | **1.92** | −0.25 frente a jev |
| llamadas de enrutamiento por turno | 0 | 1.21 | **0.97** | ya no se consulta al clasificador mini |
| tokens por turno (in / cached) | 13 513 / 13 005 | 9 095 / 7 370 | **8 514 / 6 334** | |
| costo por consulta correcta | $0.00180 | $0.00154 | **$0.00110** | **−39 % frente a legacy; −29 % frente a jev** |
| legacy_share | 100 % | 36.1 % | **26.5 %** | **meta < 25 %: no se cumple** (1.5 pts por encima) |
| latencia p50 / p95 | 0.73 / 1.05 s | 0.91 / 1.88 s | **0.89 / 1.14 s** | |

## Solo el conjunto de aceptación (`whatsapp-pilot.json`)

| variante (whatsapp-pilot.json) | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| línea base · legacy_mini | 90 | 68 (75.6 %) | — (n=0) | 75.6 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13067 | $0.00227 | 100.0 % | 0.73 s | 1.49 s |
| línea base · reference_mini | 90 | 52 (57.8 %) | 67.9 % (n=84) | 75.4 % (n=65) | 1.89 | 0.83 | 8673 / 44 / 6770 | $0.00261 | 32.2 % | 1.38 s | 2.09 s |
| línea base · jev_mini | 90 | 52 (57.8 %) | 71.4 % (n=84) | 74.2 % (n=66) | 2.08 | 1.10 | 8565 / 159 / 6855 | $0.00188 | 30.0 % | 0.89 s | 2.27 s |
| T25 · legacy_mini | 90 | 66 (73.3 %) | — (n=0) | 73.3 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13764 | $0.00205 | 100.0 % | 0.75 s | 1.20 s |
| T25 · reference_mini | 90 | 54 (60.0 %) | 66.7 % (n=84) | 79.7 % (n=64) | 1.89 | 0.83 | 8672 / 44 / 7683 | $0.00206 | 32.2 % | 1.39 s | 2.20 s |
| T25 · jev_mini | 90 | 50 (55.6 %) | 71.4 % (n=84) | 71.2 % (n=66) | 2.07 | 1.10 | 8509 / 158 / 6953 | $0.00186 | 30.0 % | 0.84 s | 1.66 s |
| **T25 · custom (todo activado)** | 90 | 66 (73.3 %) | 75.0 % (n=84) | 78.0 % (n=82) | 1.93 | 1.00 | 7963 / 389 / 5807 | $0.00111 | 21.1 % | 0.88 s | 1.14 s |

En el piloto, custom iguala a legacy en acierto total (73.3 % frente a 73.3 % en esta corrida y
75.6 % en la línea base), cuesta un 46 % menos por consulta correcta ($0.00111 frente a $0.00205) y
manda a legacy el 21.1 % de los turnos (meta < 25 % cumplida en este conjunto).

## Lectura

- **Se cumple el objetivo del plan** (bajar el costo por consulta correcta): $0.00110 frente a
  $0.00154 de la mejor variante modular de la línea base y $0.00180 de legacy.
- **No se cumple `legacy_share < 25 %`** en los tres conjuntos (26.5 %). El 75 % de los turnos de
  aclaración (`aclaracion`) va a legacy; en `clientes` es el 14.8 % y en `proveedores` el 25 %.
- **El acierto total sigue por debajo de legacy** (−1.7 pts en la misma corrida), dentro del ruido
  entre corridas pero de forma consistente. Los fallos de custom que también tiene legacy
  (`ultima_nota`, `instruction-inside-question`, `group-comparison`, `supplier-last-note-followup`,
  `pilot-bank-two-words-no-keyword`, `pilot-report-of-person-this-month`,
  `pilot-supplier-first-of-list`) no dependen del enrutamiento.
- Errores propios del enrutamiento modular que siguen: confusión clientes↔proveedores
  (`statement-dates-without-year`, `pilot-supplier-statement-range`), selección de lista o código
  (`last-payment-keeps-chosen-code`) y `gastos` (0/6: la ruta es correcta, pero la primera
  herramienta no es la esperada).
- `calibration.json` (custom): 237 decisiones con probabilidades, acierto de ruta de Jev 75.1 %,
  acierto de dominio 77.6 %.

## Por dominio y casos fallidos (T25)

**legacy_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 44 (91.7 %) | — (n=0) | 91.7 % (n=48) | 0.94 | 0.00 | 12672 / 18 / 12496 | $0.00147 | 100.0 % | 0.70 s | 0.86 s |
| bancos | 57 | 50 (87.7 %) | — (n=0) | 87.7 % (n=57) | 1.05 | 0.00 | 14220 / 27 / 13752 | $0.00183 | 100.0 % | 0.75 s | 1.45 s |
| clientes | 135 | 120 (88.9 %) | — (n=0) | 88.9 % (n=135) | 1.00 | 0.00 | 13517 / 30 / 13307 | $0.00165 | 100.0 % | 0.75 s | 1.03 s |
| compras | 3 | 3 (100.0 %) | — (n=0) | 100.0 % (n=3) | 1.00 | 0.00 | 13495 / 22 / 13312 | $0.00144 | 100.0 % | 0.81 s | 0.86 s |
| gastos | 6 | 3 (50.0 %) | — (n=0) | 50.0 % (n=6) | 1.00 | 0.00 | 13494 / 22 / 13312 | $0.00288 | 100.0 % | 0.67 s | 1.00 s |
| productos | 21 | 15 (71.4 %) | — (n=0) | 71.4 % (n=21) | 1.00 | 0.00 | 13498 / 25 / 13312 | $0.00202 | 100.0 % | 0.72 s | 0.92 s |
| proveedores | 24 | 12 (50.0 %) | — (n=0) | 50.0 % (n=24) | 1.00 | 0.00 | 13507 / 31 / 13328 | $0.00291 | 100.0 % | 0.77 s | 0.89 s |

**reference_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 39 (81.2 %) | — (n=0) | 84.6 % (n=26) | 1.81 | 0.88 | 9558 / 32 / 8648 | $0.00158 | 54.2 % | 1.34 s | 1.62 s |
| bancos | 57 | 37 (64.9 %) | 64.9 % (n=57) | 100.0 % (n=37) | 1.74 | 0.68 | 10434 / 37 / 9443 | $0.00216 | 59.6 % | 1.27 s | 1.83 s |
| clientes | 135 | 96 (71.1 %) | 80.3 % (n=132) | 82.3 % (n=113) | 1.99 | 0.98 | 6601 / 47 / 5452 | $0.00152 | 5.2 % | 1.42 s | 2.20 s |
| compras | 3 | 0 (0.0 %) | 0.0 % (n=3) | — (n=0) | 2.00 | 1.00 | 4133 / 40 / 3200 | — | 0.0 % | 1.31 s | 1.62 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 2469 / 28 / 1600 | — | 0.0 % | 0.69 s | 1.31 s |
| productos | 21 | 13 (61.9 %) | 90.5 % (n=21) | 68.4 % (n=19) | 2.00 | 1.00 | 13341 / 39 / 12349 | $0.00274 | 90.5 % | 1.36 s | 1.64 s |
| proveedores | 24 | 15 (62.5 %) | 66.7 % (n=21) | 71.4 % (n=21) | 1.92 | 1.00 | 8824 / 44 / 7627 | $0.00210 | 41.7 % | 1.39 s | 1.59 s |

**jev_mini por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 39 (81.2 %) | — (n=0) | 89.3 % (n=28) | 2.19 | 1.25 | 10470 / 157 / 8784 | $0.00154 | 58.3 % | 0.92 s | 1.47 s |
| bancos | 57 | 44 (77.2 %) | 78.9 % (n=57) | 97.8 % (n=45) | 1.96 | 0.91 | 12162 / 134 / 10950 | $0.00176 | 73.7 % | 0.84 s | 1.95 s |
| clientes | 135 | 95 (70.4 %) | 81.8 % (n=132) | 80.7 % (n=114) | 2.29 | 1.33 | 6856 / 184 / 5068 | $0.00126 | 4.4 % | 0.92 s | 1.66 s |
| compras | 3 | 0 (0.0 %) | 0.0 % (n=3) | — (n=0) | 3.00 | 2.00 | 5210 / 190 / 3115 | — | 0.0 % | 1.39 s | 1.39 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 2736 / 164 / 1024 | — | 0.0 % | 0.17 s | 0.78 s |
| productos | 21 | 15 (71.4 %) | 100.0 % (n=21) | 71.4 % (n=21) | 2.00 | 1.00 | 14580 / 175 / 13312 | $0.00209 | 100.0 % | 0.91 s | 1.11 s |
| proveedores | 24 | 13 (54.2 %) | 71.4 % (n=21) | 61.9 % (n=21) | 2.04 | 1.17 | 8678 / 181 / 7029 | $0.00188 | 37.5 % | 0.89 s | 1.48 s |

**custom por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 41 (85.4 %) | — (n=0) | 86.1 % (n=36) | 1.81 | 0.88 | 12232 / 336 / 10179 | $0.00152 | 75.0 % | 0.91 s | 1.36 s |
| bancos | 57 | 54 (94.7 %) | 71.9 % (n=57) | 100.0 % (n=54) | 2.00 | 1.00 | 7856 / 393 / 5785 | $0.00084 | 22.8 % | 0.84 s | 1.06 s |
| clientes | 135 | 107 (79.3 %) | 78.8 % (n=132) | 82.3 % (n=124) | 1.93 | 0.98 | 8111 / 383 / 5894 | $0.00110 | 14.8 % | 0.89 s | 1.14 s |
| compras | 3 | 3 (100.0 %) | 100.0 % (n=3) | 100.0 % (n=3) | 2.00 | 1.00 | 15313 / 383 / 13312 | $0.00152 | 100.0 % | 0.92 s | 0.94 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 3478 / 375 / 1600 | — | 0.0 % | 0.22 s | 0.89 s |
| productos | 21 | 21 (100.0 %) | 100.0 % (n=21) | 100.0 % (n=21) | 2.00 | 1.00 | 5073 / 390 / 2627 | $0.00063 | 0.0 % | 0.88 s | 1.09 s |
| proveedores | 24 | 16 (66.7 %) | 71.4 % (n=21) | 76.2 % (n=21) | 1.88 | 1.00 | 8329 / 390 / 5984 | $0.00138 | 25.0 % | 0.88 s | 1.09 s |

**legacy_mini** — 18 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| global-customer-count | intent-routing.json | aclaracion | 1/3 | None → legacy | consultar_negocio / contar_coincidencias |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ranking_deuda |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 3/3 | legacy → legacy | — |  |
| pilot-bank-report-without-period | whatsapp-pilot.json | bancos | 1/3 | legacy → legacy | consultar_negocio / estado_cuenta |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | legacy → legacy | consultar_negocio / saldo |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| pilot-report-of-person-this-month | whatsapp-pilot.json | clientes | 3/3 | legacy → legacy | pedir_aclaracion |  |
| pilot-stock-how-much-of | whatsapp-pilot.json | productos | 3/3 | legacy → legacy | consultar_negocio / existencias |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / saldo |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| statement-with-period | intent-routing.json | proveedores | 1/3 | legacy → legacy | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | legacy → legacy | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |
| supplier-ranking | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / ranking_deuda |  |

**reference_mini** — 37 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-petty-cash | bank-routing.json | bancos | 2/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| bank-balance-two-words | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-how-much-money | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | describir_tablas |  |
| chosen-code-is-exact | intent-routing.json | clientes | 2/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| global-customer-count | intent-routing.json | aclaracion | 1/3 | None → legacy | consultar_negocio / contar_coincidencias |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| homonym-balances | intent-routing.json | clientes | 1/3 | clientes.saldos_coincidencias → clientes.saldos_coincidencias | pedir_aclaracion |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 2/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| pilot-balance-imperative | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 3/3 | legacy → gastos.exploracion | — |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| pilot-bare-number-selection | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-code-reply-lowercase | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-ordinal-after-numbered-list | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-ordinal-keeps-pending-operation | whatsapp-pilot.json | clientes | 2/3 | clientes.ultimo_pago → clientes.saldos_coincidencias | — |  |
| pilot-stock-how-much-of | whatsapp-pilot.json | productos | 3/3 | legacy → legacy | consultar_negocio / existencias |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 2/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 1/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| purchases-stay-exploratory | intent-routing.json | compras | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 2/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| sales-by-customer | intent-routing.json | aclaracion | 3/3 | None → clientes.total_pagos | pedir_aclaracion |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| sales-this-month | intent-routing.json | productos | 2/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| statement-dates-followup | intent-routing.json | clientes | 1/3 | clientes.estado_cuenta → clientes.estado_cuenta | consultar_negocio / estado_cuenta |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |

**jev_mini** — 32 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 3/3 | legacy → clientes.saldo | consultar_negocio / saldo |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 3/3 | legacy → gastos.exploracion | describir_tablas |  |
| chosen-code-is-exact | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| homonym-balances | intent-routing.json | clientes | 1/3 | clientes.saldos_coincidencias → clientes.saldos_coincidencias | pedir_aclaracion |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 2/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| pilot-balance-imperative | whatsapp-pilot.json | clientes | 3/3 | clientes.saldo → clientes.saldos_coincidencias | consultar_negocio / saldos_coincidencias |  |
| pilot-bank-report-followup-typo | whatsapp-pilot.json | bancos | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| pilot-bank-report-without-period | whatsapp-pilot.json | bancos | 1/3 | legacy → legacy | consultar_negocio / estado_cuenta |  |
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
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| purchases-stay-exploratory | intent-routing.json | compras | 3/3 | legacy → gastos.exploracion | pedir_aclaracion |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 3/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| sales-by-customer | intent-routing.json | aclaracion | 3/3 | None → clientes.total_pagos | pedir_aclaracion |  |
| sales-report-is-not-a-statement | intent-routing.json | productos | 3/3 | legacy → legacy | pedir_aclaracion |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| supplier-movements-followup | intent-routing.json | proveedores | 3/3 | legacy → legacy | consultar_negocio / movimientos |  |

**custom** — 19 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| global-customer-count | intent-routing.json | aclaracion | 2/3 | None → legacy | consultar_negocio / contar_coincidencias |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 3/3 | clientes.saldo → legacy | consultar_negocio / ranking_deuda |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 3/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | bancos.saldo_banco → clientes.saldo | consultar_negocio / saldo |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-report-of-person-this-month | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → legacy | pedir_aclaracion |  |
| pilot-statement-range-without-year | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | proveedores.saldo → legacy | consultar_negocio / saldo |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 3/3 | proveedores.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 2/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → proveedores.estado_cuenta | pedir_aclaracion |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → proveedores.ultima_nota | consultar_negocio / ultima_nota |  |
