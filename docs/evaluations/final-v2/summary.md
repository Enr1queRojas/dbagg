# Evaluación final v2 (T25 repetida tras 854aa68 y 210fce0) — 2026-11-12

Qué cambió en el código después de T25:

- `854aa68`: el dominio `proveedores` solo registra `proveedores.movimientos`; los saldos, notas y
  estados de cuenta de proveedores van por las rutas de `clientes` con `entidad = proveedor`. Además,
  hay una pista para rangos sin año que terminan hoy.
- `210fce0`: después de «Cuenta X», la lista de personas con ese nombre también ofrece la cuenta
  bancaria.

Para esta repetición no se cambió código: solo se volvió a calibrar en desarrollo y se repitió T25
una vez. Medido con API (OpenAI `gpt-4.1-mini`, TypeSafe `jev-1.14.0`), `--repeat 3`. Todas las
variantes quedaron `measured`, sin uso desconocido.

**Las rutas esperadas cambiaron.** Como `proveedores` ahora solo tiene `movimientos`, los casos de
saldo, nota o estado de cuenta de proveedores esperan la ruta de `clientes` (p. ej.
`pilot-supplier-first-of-list`: `proveedores.saldo` en T25 → `clientes.saldo` ahora). Por eso
`route_accuracy` (y la calibración de Jev) **no es comparable uno a uno** con T25. El acierto total,
los parámetros, las llamadas, el costo y `legacy_share` sí son comparables.

## 1. Recalibración en desarrollo (`docs/evaluations/thresholds/development-v2/`)

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/modular-development.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/thresholds/development-v2 --repeat 3 --variants custom --config docs/evaluations/thresholds/llm.observe.toml
python -m dbagg.evaluation.calibration --input docs/evaluations/thresholds/development-v2/benchmark.json --variant custom --output docs/evaluations/thresholds/development-v2/calibration.json
```

Costo: $0.248. Misma config de observación que en T13 (5 dominios, `min_confidence = 0.8`, sin
margen ni nivel de dominio):

| corrida (desarrollo, 252 turnos) | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | cost_per_correct_usd | legacy_share |
|---|---|---|---|---|---|---|---|
| T13 (`development-all-domains`) | 216 (85.7 %) | 86.3 % (n=183) | 93.3 % (n=208) | 2.29 | 1.36 | $0.00172 | 19.8 % |
| v2 (`development-v2`) | 210 (83.3 %) | 86.3 % (n=183) | 91.1 % (n=203) | 2.21 | 1.28 | $0.00118 | 17.9 % |

`calibration.json`: 180 decisiones de Jev con probabilidades; acierto de ruta 85.6 % (T13: 81.1 %)
y acierto de dominio 87.8 %.

Curva de la suma de probabilidades por dominio. Es lo que compara `min_domain_confidence`, con la
misma regla que `tiered_decision`: rutas del dominio con probabilidad > 0.05, máximo 3. Reproduce
exactamente la tabla de T13 cuando se aplica a `development-all-domains`.

| min_domain_confidence | cobertura | dominio correcto | ruta esperada entre las candidatas |
|---|---|---|---|
| 0.20 | 100.0 % | 95.6 % | 95.6 % |
| 0.30 | 96.1 % | 96.5 % | 96.5 % |
| **0.40** | **91.7 %** | **97.0 %** | **97.0 %** |
| 0.50 | 88.3 % | 97.5 % | 97.5 % |
| 0.60 | 78.9 % | 97.9 % | 97.9 % |
| 0.80 | 67.2 % | 98.3 % | 98.3 % |
| 0.90 | 61.1 % | 100.0 % | 100.0 % |

Decisión simulada en tres niveles. Cuenta como ruta correcta si la operación coincide, si la ruta
esperada está entre las candidatas del dominio o si se esperaba `legacy` y se fue a legacy:

| min_confidence | min_margin | min_domain_confidence | ruta correcta | operación (cobertura @ acierto) | dominio (cobertura @ acierto) | legacy (aciertos) |
|---|---|---|---|---|---|---|
| 0.8 | 0–0.4 | sin nivel de dominio | 65.6 % | 65 % @ 98.3 % | — | 35.0 % (3/63) |
| 0.8 | 0.2 | 0.2 | 95.6 % | 65 % @ 98.3 % | 35 % @ 90.5 % | 0.0 % (0/0) |
| 0.8 | 0.2 | 0.3 | 93.9 % | 65 % @ 98.3 % | 31 % @ 92.9 % | 3.9 % (2/7) |
| **0.8** | **0.2** | **0.4** | **90.6 %** | 65 % @ 98.3 % | 27 % @ 93.8 % | 8.3 % (3/15) |
| 0.8 | 0.2 | 0.5 | 87.8 % | 65 % @ 98.3 % | 23 % @ 95.2 % | 11.7 % (3/21) |
| 0.8 | 0.2 | 0.6 | 78.9 % | 65 % @ 98.3 % | 14 % @ 96.0 % | 21.1 % (3/38) |
| 0.7 | 0.2 | 0.4 | 90.6 % | 69 % @ 97.6 % | 23 % @ 95.1 % | 8.3 % (3/15) |
| 0.9 | 0.2 | 0.4 | 90.6 % | 56 % @ 100.0 % | 36 % @ 92.3 % | 8.3 % (3/15) |

**Elección (solo con desarrollo): `min_domain_confidence = 0.4`, igual que en T13.**

- Las 3 decisiones que esperan `legacy` (`purchases-stay-exploratory`) siguen en legacy con 0.4.
  Con 0.3 una de ellas ya se manda a un módulo (2/7), y con 0.2 las tres. El riesgo que en T13 era
  una suposición ahora se observa en la curva.
- Entre 0.4 y 0.5, 0.4 rescata ~5 decisiones más (90.6 % frente a 87.8 % de ruta correcta). El
  acierto de las candidatas de dominio sigue por encima del 93 %.
- `min_confidence = 0.8` se mantiene: 98.3 % de acierto con 65 % de cobertura. 0.7 y 0.9 dan la
  misma ruta correcta simulada; solo cambian la parte que va al módulo de operación y la que va al
  de dominio.
- `min_margin = 0.2` se mantiene: con `min_confidence = 0.8` no cambia ninguna decisión.

Por tanto, `final-v2/llm.all.toml` es una copia de `final/llm.all.toml` con los mismos umbrales
(solo cambia el comentario de cabecera).

## 2. Evaluación final v2

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/final-v2 --repeat 3 --variants legacy_mini reference_mini jev_mini custom --config docs/evaluations/final-v2/llm.all.toml
python -m dbagg.evaluation.calibration --input docs/evaluations/final-v2/benchmark.json --variant custom --output docs/evaluations/final-v2/calibration.json
```

Se ejecutó una sola vez. Costo: $1.39 (legacy $0.437, reference $0.357, jev $0.322, custom
$0.277); T25 costó $1.38.

### Los tres conjuntos (294 turnos por variante)

| variante | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T25 · legacy_mini | 294 | 247 (84.0 %) | — (n=0) | 84.0 % (n=294) | 1.00 | 0.00 | 13513 / 27 / 13263 | $0.00175 | 100.0 % | 0.73 s | 1.03 s |
| T25 · reference_mini | 294 | 200 (68.0 %) | 75.8 % (n=240) | 81.1 % (n=222) | 1.89 | 0.91 | 8380 / 41 / 7316 | $0.00180 | 32.7 % | 1.38 s | 1.74 s |
| T25 · jev_mini | 294 | 206 (70.1 %) | 81.2 % (n=240) | 80.4 % (n=235) | 2.16 | 1.20 | 9074 / 169 / 7461 | $0.00155 | 36.1 % | 0.89 s | 1.58 s |
| T25 · custom | 294 | 242 (82.3 %) | 79.2 % (n=240) | 85.7 % (n=265) | 1.92 | 0.97 | 8514 / 378 / 6334 | $0.00110 | 26.5 % | 0.89 s | 1.14 s |
| v2 · legacy_mini | 294 | 251 (85.4 %) | — (n=0) | 85.4 % (n=294) | 1.00 | 0.00 | 13513 / 27 / 13209 | $0.00174 | 100.0 % | 0.72 s | 1.05 s |
| v2 · reference_mini | 294 | 200 (68.0 %) | 75.8 % (n=240) | 81.4 % (n=221) | 1.89 | 0.91 | 8335 / 41 / 7283 | $0.00179 | 32.3 % | 1.25 s | 1.69 s |
| v2 · jev_mini | 294 | 210 (71.4 %) | 81.2 % (n=240) | 81.2 % (n=234) | 2.16 | 1.20 | 9048 / 169 / 7388 | $0.00153 | 35.7 % | 0.89 s | 1.62 s |
| **v2 · custom** | 294 | 240 (81.6 %) | 84.2 % (n=240) | 83.7 % (n=270) | 1.92 | 0.97 | 7883 / 248 / 5882 | $0.00115 | **24.1 %** | 0.84 s | 1.09 s |

| métrica (custom) | T25 | **v2** | diferencia / lectura |
|---|---|---|---|
| acierto total | 82.3 % (242) | **81.6 % (240)** | −2 turnos; dentro del ruido entre corridas (2–3 pts) |
| frente a legacy en la misma corrida | −1.7 pts | **−3.7 pts** | legacy subió 84.0 → 85.4 % sin cambios propios: ruido |
| route_accuracy | 79.2 % | **84.2 %** | no comparable: cambiaron las rutas esperadas de proveedores |
| arguments_accuracy_given_route | 85.7 % (n=265) | **83.7 % (n=270)** | −2.0 pts sobre 5 turnos más con ruta correcta |
| llamadas por turno | 1.92 | **1.92** | igual |
| llamadas de enrutamiento por turno | 0.97 | **0.97** | igual |
| tokens por turno (in / out / cached) | 8 514 / 378 / 6 334 | **7 883 / 248 / 5 882** | −7 % de entrada |
| costo por consulta correcta | $0.00110 | **$0.00115** | +5 %; sigue −34 % frente a legacy ($0.00174) y −25 % frente a jev ($0.00153) |
| legacy_share (meta < 25 %) | 26.5 % ✗ | **24.1 % ✔** | se cumple por 0.9 pts (≈ 3 turnos de 294): margen dentro del ruido |
| latencia p50 / p95 | 0.89 / 1.14 s | **0.84 / 1.09 s** | |
| casos con algún fallo | 19 | **24** | la mayoría de los nuevos fallan 1 de 3 (ver §3) |

### Solo el conjunto de aceptación (`whatsapp-pilot.json`, 90 turnos)

| variante (whatsapp-pilot.json) | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T25 · legacy_mini | 90 | 66 (73.3 %) | — (n=0) | 73.3 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13764 | $0.00205 | 100.0 % | 0.75 s | 1.20 s |
| T25 · custom | 90 | 66 (73.3 %) | 75.0 % (n=84) | 78.0 % (n=82) | 1.93 | 1.00 | 7963 / 389 / 5807 | $0.00111 | 21.1 % | 0.88 s | 1.14 s |
| v2 · legacy_mini | 90 | 68 (75.6 %) | — (n=0) | 75.6 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13561 | $0.00207 | 100.0 % | 0.72 s | 1.14 s |
| v2 · reference_mini | 90 | 53 (58.9 %) | 67.9 % (n=84) | 78.1 % (n=64) | 1.88 | 0.83 | 8521 / 43 / 7437 | $0.00212 | 31.1 % | 1.25 s | 2.05 s |
| v2 · jev_mini | 90 | 52 (57.8 %) | 71.4 % (n=84) | 74.2 % (n=66) | 2.07 | 1.10 | 8509 / 158 / 6919 | $0.00180 | 30.0 % | 0.86 s | 1.69 s |
| **v2 · custom** | 90 | 65 (72.2 %) | 78.6 % (n=84) | 73.8 % (n=84) | 1.93 | 1.00 | 7437 / 255 / 5362 | $0.00124 | 20.0 % | 0.84 s | 1.08 s |

En el piloto, custom pasa de igualar a legacy (73.3 % frente a 73.3 %) a quedar 3.4 pts por debajo
(72.2 % frente a 75.6 %): −1 turno propio y +2 de legacy. Cuesta un 40 % menos por consulta correcta
($0.00124 frente a $0.00207) y manda a legacy el 20.0 % de los turnos.

### custom por dominio (T25 → v2)

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | legacy_share |
|---|---|---|---|---|---|
| aclaracion | 48 | 41 → 43 (85.4 → 89.6 %) | — | 86.1 → 91.4 % | 75.0 → 72.9 % |
| bancos | 57 | 54 → 52 (94.7 → 91.2 %) | 71.9 → 77.2 % | 100.0 → 98.1 % | 22.8 → 15.8 % |
| clientes | 135 | 107 → 108 (79.3 → 80.0 %) | 78.8 → 85.6 % | 82.3 → 80.2 % | 14.8 → 13.3 % |
| compras | 3 | 3 → 3 | 100 → 100 % | 100 → 100 % | 100 → 100 % |
| gastos | 6 | 0 → 0 | 100 → 100 % | 0 → 0 % | 0 → 0 % |
| productos | 21 | 21 → 21 | 100 → 100 % | 100 → 100 % | 0 → 0 % |
| **proveedores** | 24 | **16 → 13 (66.7 → 54.2 %)** | 71.4 → 71.4 % | 76.2 → 61.9 % | 25.0 → 25.0 % |

Tabla completa por dominio de v2:

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| aclaracion | 48 | 43 (89.6 %) | — (n=0) | 91.4 % (n=35) | 1.81 | 0.88 | 11693 / 218 / 10064 | $0.00140 | 72.9 % | 0.81 s | 1.05 s |
| bancos | 57 | 52 (91.2 %) | 77.2 % (n=57) | 98.1 % (n=53) | 2.00 | 1.00 | 6665 / 258 / 4810 | $0.00084 | 15.8 % | 0.81 s | 1.16 s |
| clientes | 135 | 108 (80.0 %) | 85.6 % (n=132) | 80.2 % (n=131) | 1.93 | 0.98 | 7615 / 252 / 5392 | $0.00122 | 13.3 % | 0.86 s | 1.09 s |
| compras | 3 | 3 (100.0 %) | 100.0 % (n=3) | 100.0 % (n=3) | 2.00 | 1.00 | 14873 / 249 / 13269 | $0.00151 | 100.0 % | 0.77 s | 0.80 s |
| gastos | 6 | 0 (0.0 %) | 100.0 % (n=6) | 0.0 % (n=6) | 1.50 | 1.00 | 3038 / 240 / 1387 | — | 0.0 % | 0.20 s | 0.92 s |
| productos | 21 | 21 (100.0 %) | 100.0 % (n=21) | 100.0 % (n=21) | 2.00 | 1.00 | 4633 / 256 / 2609 | $0.00062 | 0.0 % | 0.83 s | 1.00 s |
| proveedores | 24 | 13 (54.2 %) | 71.4 % (n=21) | 61.9 % (n=21) | 1.88 | 1.00 | 7839 / 255 / 5883 | $0.00168 | 25.0 % | 0.86 s | 1.02 s |

`calibration.json` (custom): 237 decisiones con probabilidades, acierto de ruta de Jev 81.0 % (T25:
75.1 %) y acierto de dominio 83.5 % (T25: 77.6 %). No es comparable por el cambio de rutas
esperadas.

## 3. Casos fallidos

### custom: casos cuyo número de fallos cambió (T25 → v2)

| caso | dominio | fallos T25 → v2 | ruta esperada → elegida (v2) | lectura |
|---|---|---|---|---|
| **statement-with-period** | proveedores | **0/3 → 3/3** | clientes.estado_cuenta → proveedores.movimientos | **regresión de 854aa68**: «Reporte del proveedor Cuervo de este mes» va al único módulo de proveedores y responde con `movimientos` en vez de `estado_cuenta` |
| bank-balance-account-of | bancos | 0/3 → 1/3 | bancos.saldo_banco → bancos.saldo_banco | `pedir_aclaracion` en 1 rep |
| bank-withdrawals-last-week | bancos | 0/3 → 1/3 | bancos.movimientos_banco → gastos.exploracion | 1 rep |
| pilot-code-reply-lowercase | clientes | 0/3 → 1/3 | clientes.saldo → clientes.saldo | 1 rep |
| sales-by-customer | aclaracion | 0/3 → 1/3 | None → clientes.ultima_nota | 1 rep |
| statement-dates-followup | clientes | 0/3 → 1/3 | clientes.estado_cuenta → clientes.estado_cuenta | 1 rep |
| global-customer-count | aclaracion | 2/3 → 0/3 | None → legacy | mejora |
| instruction-inside-question | clientes | 3/3 → 2/3 | clientes.saldo → legacy | mejora parcial |
| last-payment-keeps-chosen-code | clientes | 3/3 → 1/3 | clientes.ultimo_pago → clientes.ultimo_pago | mejora parcial |
| rank-is-not-selection | aclaracion | 2/3 → 1/3 | None → clientes.ultimo_pago | mejora parcial |

Lo que se esperaba de los cambios y no se ve:

- **La pista de rangos que terminan hoy no corrige los dos casos de rangos sin año.**
  `pilot-statement-range-without-year` y `statement-dates-without-year` siguen fallando 3/3 con
  `pedir_aclaracion`, igual que en T25.
- **Proveedores sigue con sus fallos de siempre** y añade la regresión de `statement-with-period`:
  `pilot-supplier-first-of-list` (3/3, ahora a legacy), `pilot-supplier-statement-range` (3/3,
  `pedir_aclaracion`) y `pilot-vague-review-suppliers` (2/3).
- `pilot-bank-two-words-no-keyword` sigue 3/3 (a `clientes.saldo`). La oferta de cuenta de
  `210fce0` aplica después de «Cuenta X», que este caso no tiene.

En las variantes de referencia, los cambios entre T25 y v2 son de 1–2 repeticiones por caso
(legacy: 5 casos; reference: 8; jev: 5), consistentes con el ruido entre corridas. legacy_mini no
pasa por el enrutamiento modular. Los fallos completos por variante están en `benchmark.html`.

### custom (v2): 24 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| balance-does-not-inherit-period | intent-routing.json | clientes | 3/3 | clientes.saldo → clientes.total_pagos | consultar_negocio / saldo |  |
| bank-balance-account-of | bank-routing.json | bancos | 1/3 | bancos.saldo_banco → bancos.saldo_banco | pedir_aclaracion |  |
| bank-withdrawals-last-week | bank-routing.json | bancos | 1/3 | bancos.movimientos_banco → gastos.exploracion | describir_tablas |  |
| expense-balance-explores | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | pedir_aclaracion |  |
| expenses-report-is-not-a-statement | intent-routing.json | gastos | 3/3 | gastos.exploracion → gastos.exploracion | describir_tablas |  |
| group-comparison | intent-routing.json | aclaracion | 3/3 | None → legacy | consultar_negocio / saldos_coincidencias |  |
| instruction-inside-question | intent-routing.json | clientes | 2/3 | clientes.saldo → legacy | consultar_negocio / ranking_deuda |  |
| last-payment-keeps-chosen-code | intent-routing.json | clientes | 1/3 | clientes.ultimo_pago → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | bancos.saldo_banco → clientes.saldo | consultar_negocio / saldo |  |
| pilot-code-reply-lowercase | whatsapp-pilot.json | clientes | 1/3 | clientes.saldo → clientes.saldo | consultar_negocio / saldo |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-report-of-person-this-month | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → legacy | pedir_aclaracion |  |
| pilot-statement-range-without-year | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | clientes.saldo → legacy | consultar_negocio / saldo |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 2/3 | None → legacy | describir_tablas |  |
| pilot-voice-followup-last-purchase | whatsapp-pilot.json | clientes | 1/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| rank-is-not-selection | intent-routing.json | aclaracion | 1/3 | None → clientes.ultimo_pago | consultar_negocio / ultimo_pago |  |
| sales-by-customer | intent-routing.json | aclaracion | 1/3 | None → clientes.ultima_nota | pedir_aclaracion |  |
| statement-dates-followup | intent-routing.json | clientes | 1/3 | clientes.estado_cuenta → clientes.estado_cuenta | consultar_negocio / estado_cuenta |  |
| statement-dates-without-year | intent-routing.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| statement-with-period | intent-routing.json | proveedores | 3/3 | clientes.estado_cuenta → proveedores.movimientos | consultar_negocio / movimientos |  |
| supplier-last-note-followup | intent-routing.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |

## 4. Lectura

- **Umbrales:** la recalibración en desarrollo confirma los de T13 (`0.8 / 0.2 / 0.4`).
- **Meta `legacy_share < 25 %`: se cumple por primera vez** (24.1 %), pero por menos de 1 pt
  (≈ 3 turnos de 294), es decir, dentro del ruido. Bajó en `bancos` (22.8 → 15.8 %) y `clientes`
  (14.8 → 13.3 %).
- **Costo por consulta correcta:** $0.00115, +5 % frente a T25 ($0.00110). Sigue un 34 % por debajo
  de legacy y un 25 % por debajo de `jev_mini` de la misma corrida.
- **Acierto total:** 81.6 % frente a 82.3 % en T25. La brecha con legacy en la misma corrida pasa de
  −1.7 a −3.7 pts, en parte porque legacy subió sin cambios propios (84.0 → 85.4 %).
- **Regresión concreta que conviene revisar:** `statement-with-period` (0/3 → 3/3). Pedir el reporte
  de un proveedor con periodo ahora entra al módulo `proveedores.movimientos` y responde con
  `movimientos`. Es la causa principal de la caída de `proveedores` (16 → 13 aprobados). No se
  corrigió aquí porque la tarea era no tocar código.
