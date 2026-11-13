# Calibración de umbrales (T13) — 2026-11-12

**Valores elegidos (calibrados para estos conjuntos, no garantía de producción):**

```toml
min_confidence = 0.8
min_margin = 0.2
min_domain_confidence = 0.4
```

Config completa: `llm.t13.toml` (5 dominios, `compact_history = true`, `stable_prefix = false`,
`fixed_tool_schema = false`). **Verificación en el piloto: se acepta** (ruta 73.8 % ≥ 71.4 % de la
línea base; 1.00 llamadas de enrutamiento por turno ≤ 1.1).

## 1. Mediciones en desarrollo

Conjuntos `modular-development.json` (16 casos), `intent-routing.json` (55) y `bank-routing.json`
(13), `--repeat 3`: 252 turnos por variante. Todas las variantes quedaron `measured`.

- `development/`: los comandos de `PENDIENTE.md` (variantes `legacy_mini`, `reference_mini` y
  `jev_mini`, esta última con los dominios por defecto `clientes` y `gastos`). Calibración en
  `development/calibration.json`.
- `development-all-domains/`: **corrida adicional** con `llm.observe.toml`, igual que `jev_mini`
  (`min_confidence = 0.8`, sin margen ni nivel de dominio) pero con los 5 dominios de la config
  final. Jev reparte la probabilidad entre las rutas registradas, así que las curvas dependen del
  conjunto de rutas: los umbrales se eligieron con esta corrida. Calibración en
  `development-all-domains/calibration.json`.

| corrida | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| development · legacy_mini | 252 | 228 (90.5 %) | — (n=0) | 90.5 % (n=252) | 0.99 | 0.00 | 13346 / 26 / 12798 | $0.00170 | 100.0 % | 0.72 s | 1.02 s |
| development · reference_mini | 252 | 196 (77.8 %) | 83.1 % (n=183) | 87.1 % (n=194) | 1.87 | 0.94 | 7968 / 40 / 6386 | $0.00172 | 30.2 % | 1.38 s | 1.67 s |
| development · jev_mini | 252 | 206 (81.7 %) | 88.5 % (n=183) | 87.7 % (n=204) | 2.17 | 1.25 | 8887 / 173 / 7192 | $0.00132 | 34.5 % | 0.91 s | 1.72 s |
| observación con 5 dominios (`development-all-domains`) | 252 | 216 (85.7 %) | 86.3 % (n=183) | 93.3 % (n=208) | 2.29 | 1.36 | 8459 / 379 / 4459 | $0.00172 | 19.8 % | 1.01 s | 1.80 s |
| umbrales elegidos (`experiments/base`) | 252 | 221 (87.7 %) | 84.2 % (n=183) | 90.4 % (n=218) | 1.88 | 0.95 | 8494 / 371 / 6039 | $0.00114 | 26.6 % | 0.88 s | 1.16 s |

## 2. Curvas (desarrollo con 5 dominios: 180 decisiones de Jev con probabilidades)

Acierto de ruta de Jev: 81.1 %. Acierto de dominio por argmax: 82.8 %.

| umbral | cobertura (confianza) | acierto (confianza) | cobertura (margen) | acierto (margen) |
|---|---|---|---|---|
| 0.50 | 90.6 % | 82.2 % | 67.2 % | 88.4 % |
| 0.60 | 78.3 % | 83.7 % | 57.8 % | 93.3 % |
| 0.70 | 65.6 % | 88.1 % | 52.8 % | 93.7 % |
| 0.75 | 59.4 % | 93.5 % | 48.9 % | 93.2 % |
| **0.80** | **55.6 %** | **94.0 %** | 45.0 % | 93.8 % |
| 0.85 | 48.9 % | 93.2 % | 41.7 % | 96.0 % |
| 0.90 | 42.8 % | 96.1 % | 36.1 % | 95.4 % |
| 0.95 | 33.9 % | 98.4 % | 20.6 % | 100.0 % |

`calibration.json` no incluye la curva de la suma de probabilidades por dominio, que es lo que
compara `min_domain_confidence`. La calculé desde `benchmark.json` con la misma regla que
`tiered_decision` (`agent/service.py`: rutas del dominio con probabilidad > 0.05, máximo 3):

| min_domain_confidence | cobertura | dominio correcto | ruta esperada entre las candidatas |
|---|---|---|---|
| 0.30 | 93.3 % | 94.6 % | 94.6 % |
| **0.40** | **88.3 %** | **94.3 %** | **94.3 %** |
| 0.50 | 81.7 % | 93.9 % | 93.9 % |
| 0.60 | 73.9 % | 93.2 % | 93.2 % |
| 0.80 | 57.2 % | 94.2 % | 94.2 % |
| 0.90 | 47.2 % | 92.9 % | 92.9 % |

Simulé la decisión completa en tres niveles sobre las mismas 180 decisiones. Cuenta como ruta
correcta si la operación coincide, si la esperada está entre las candidatas del dominio o si se
esperaba `legacy` y se fue a legacy:

| min_confidence | min_margin | min_domain_confidence | ruta correcta | operación (cobertura @ acierto) | dominio (cobertura @ acierto) | legacy |
|---|---|---|---|---|---|---|
| 0.8 | 0–0.4 | sin nivel de dominio | 53.9 % | 56 % @ 94.0 % | — | 44.4 % |
| 0.8 | 0.2 | 0.8 | 55.6 % | 56 % @ 94.0 % | 2 % @ 100 % | 42.8 % |
| 0.6 | 0 | 0.5 | 78.3 % | 69 % @ 92.7 % | 13 % @ 100 % | 18.3 % |
| 0.9 | 0 | 0.5 | 78.3 % | 43 % @ 96.1 % | 39 % @ 91.4 % | 18.3 % |
| 0.8 | 0.2 | 0.5 | 78.3 % | 56 % @ 94.0 % | 26 % @ 93.6 % | 18.3 % |
| **0.8** | **0.2** | **0.4** | **85.0 %** | 56 % @ 94.0 % | 33 % @ 94.9 % | 11.7 % |
| 0.8 | 0.2 | 0.3 | 90.0 % | 56 % @ 94.0 % | 38 % @ 95.6 % | 6.7 % |

## 3. Justificación

- **`min_confidence = 0.8`.** Con el nivel de dominio activo, la ruta correcta simulada es la misma
  para cualquier valor entre 0.5 y 0.9: los errores que quedan son confusiones clientes↔proveedores
  con alta confianza, que ningún umbral corrige. El umbral solo decide qué parte va al módulo de
  una operación (prompt más corto) y qué parte al de dominio (hasta 3 operaciones). En 0.8 la curva
  da 94.0 % de acierto con 55.6 % de cobertura. Por debajo, el acierto cae a 88.1 % en 0.70 y a
  83.7 % en 0.60. En 0.9 sube a 96.1 %, pero manda el 39 % de los turnos al módulo de dominio, cuyo
  acierto baja a 91.4 %.
- **`min_margin = 0.2`.** No restringe nada con `min_confidence = 0.8`: si la primera ruta tiene
  ≥ 0.8, la segunda tiene ≤ 0.2 y el margen es ≥ 0.6. En la simulación, valores de 0 a 0.4 no
  cambian ni una decisión. Lo dejo en 0.2 como salvaguarda por si la confianza informada no
  coincide con la primera probabilidad.
- **`min_domain_confidence = 0.4`.** Es la palanca principal. Entre 0.3 y 0.8 el acierto de las
  candidatas se mantiene estable (93–95 %), y bajar el umbral rescata turnos que si no irían a
  legacy: 53.9 % sin nivel de dominio, 78.3 % en 0.5, 85.0 % en 0.4 y 90.0 % en 0.3. No elegí 0.3,
  aunque simule mejor: los turnos que añade son aquellos en que Jev da casi toda la probabilidad a
  `legacy`, y el desarrollo tiene **un solo caso** que espera `legacy` (`purchases-stay-exploratory`)
  con el que medir el riesgo de mandar a un módulo lo que debía ir a legacy. 0.4 es un compromiso
  tomado con pocos datos.
- **Resultado en desarrollo con los umbrales elegidos** (`experiments/base`, frente a la
  observación con 5 dominios): las llamadas de enrutamiento bajan de 1.36 a 0.95 por turno (ya no
  se consulta al clasificador mini) y el costo por consulta correcta de $0.00172 a $0.00114. La
  ruta baja de 86.3 % a 84.2 %, porque la observación se apoyaba en el clasificador mini, y el
  acierto total sube de 85.7 % a 87.7 %.

Comprobación con los dominios por defecto (`development/calibration.json`, 177 decisiones): en
0.80 la curva da 71.8 % de cobertura y 97.6 % de acierto, la misma zona de la meseta.

## 4. Verificación única en el conjunto de aceptación

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --output docs/evaluations/thresholds/acceptance --repeat 3 --variants custom --config docs/evaluations/thresholds/llm.t13.toml
```

Se ejecutó una sola vez, después de fijar los valores. Comparación con la línea base, solo sobre
`whatsapp-pilot.json` (de `docs/evaluations/pilot-baseline/summary.md`):

| variante (whatsapp-pilot.json) | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| línea base · legacy_mini | 90 | 68 (75.6 %) | — (n=0) | 75.6 % (n=90) | 1.03 | 0.00 | 13977 / 28 / 13067 | $0.00227 | 100.0 % | 0.73 s | 1.49 s |
| línea base · reference_mini | 90 | 52 (57.8 %) | 67.9 % (n=84) | 75.4 % (n=65) | 1.89 | 0.83 | 8673 / 44 / 6770 | $0.00261 | 32.2 % | 1.38 s | 2.09 s |
| línea base · jev_mini | 90 | 52 (57.8 %) | 71.4 % (n=84) | 74.2 % (n=66) | 2.08 | 1.10 | 8565 / 159 / 6855 | $0.00188 | 30.0 % | 0.89 s | 2.27 s |
| **T13 (umbrales elegidos)** | 90 | 65 (72.2 %) | 73.8 % (n=84) | 77.1 % (n=83) | 1.96 | 1.00 | 8268 / 389 / 5474 | $0.00144 | 23.3 % | 0.91 s | 1.22 s |

**Criterio:** ruta 73.8 % ≥ 71.4 % (`jev_mini`) y ≥ 67.9 % (`reference_mini`) ✔;
`routing_calls_per_turn` = 1.00 ≤ 1.1 ✔. **Se acepta.** Salvedades:

- El acierto total en el piloto (72.2 %) sigue por debajo de `legacy_mini` (75.6 %). El criterio
  de T13 no lo exige, pero conviene tenerlo presente.
- La línea base calculó las rutas esperadas con 2 dominios y T13 con 5: los casos de bancos,
  proveedores y productos esperan otra ruta, así que los dos aciertos de ruta no miden exactamente
  lo mismo.
- Una diferencia de 2–3 puntos equivale a 1 o 2 casos en 3 repeticiones de 28 casos puntuables.

### Por dominio y casos fallidos (aceptación)

**custom por dominio**

| dominio | runs | aprobados | route_accuracy | arguments_acc_given_route | calls/turno | routing_calls/turno | tokens/turno (in/out/cached) | cost_per_correct_usd | legacy_share | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bancos | 24 | 20 (83.3 %) | 83.3 % (n=24) | 95.2 % (n=21) | 2.08 | 1.00 | 6630 / 393 / 3920 | $0.00103 | 4.2 % | 0.89 s | 1.48 s |
| clientes | 48 | 36 (75.0 %) | 73.3 % (n=45) | 74.5 % (n=47) | 1.94 | 1.00 | 9128 / 388 / 6144 | $0.00157 | 29.2 % | 0.91 s | 1.14 s |
| productos | 6 | 6 (100.0 %) | 100.0 % (n=6) | 100.0 % (n=6) | 2.00 | 1.00 | 4852 / 388 / 1899 | $0.00075 | 0.0 % | 0.89 s | 1.03 s |
| proveedores | 12 | 3 (25.0 %) | 33.3 % (n=9) | 33.3 % (n=9) | 1.75 | 1.00 | 9810 / 385 / 7691 | $0.00397 | 50.0 % | 0.94 s | 1.14 s |

**custom** — 9 casos con algún fallo

| caso | dataset | dominio | fallos/rep | ruta esperada → elegida | herramienta / operación obtenida | error |
|---|---|---|---|---|---|---|
| pilot-bank-instead-of-listed-party | whatsapp-pilot.json | bancos | 1/3 | bancos.saldo_banco → bancos.saldo_banco | pedir_aclaracion |  |
| pilot-bank-two-words-no-keyword | whatsapp-pilot.json | bancos | 3/3 | bancos.saldo_banco → clientes.saldo | consultar_negocio / saldo |  |
| pilot-last-note-is-not-last-payment | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-last-purchase-note-by-name | whatsapp-pilot.json | clientes | 3/3 | clientes.ultima_nota → clientes.ultima_nota | consultar_negocio / ultima_nota |  |
| pilot-report-of-person-this-month | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → legacy | pedir_aclaracion |  |
| pilot-statement-range-without-year | whatsapp-pilot.json | clientes | 3/3 | clientes.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-supplier-first-of-list | whatsapp-pilot.json | proveedores | 3/3 | proveedores.saldo → legacy | consultar_negocio / saldo |  |
| pilot-supplier-statement-range | whatsapp-pilot.json | proveedores | 3/3 | proveedores.estado_cuenta → clientes.estado_cuenta | pedir_aclaracion |  |
| pilot-vague-review-suppliers | whatsapp-pilot.json | proveedores | 3/3 | None → legacy | describir_tablas |  |
