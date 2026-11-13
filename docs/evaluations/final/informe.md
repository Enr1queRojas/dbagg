# Informe final — prompts y enrutamiento con Jev (2026-11-12)

Rama `claude/blissful-mayer-xgcf54`. Suite: 623 pruebas al inicio (T0) → 707 al cierre, todas en
verde; `ruff check` y `ruff format --check` limpios. Las mediciones con API (T3, T13, T17–T19 y
T25) se ejecutaron en una segunda sesión el mismo día; costo estimado total de esas corridas:
**$4.83**. No hay cifras simuladas.

## Resultado medido (T25 frente a la línea base T3)

`whatsapp-pilot.json` + `intent-routing.json` + `bank-routing.json`, `--repeat 3`, 294 turnos por
variante (`docs/evaluations/final/summary.md`):

| métrica | línea base legacy | línea base jev_mini | final «todo activado» |
| --- | --- | --- | --- |
| acierto total | 85.7 % | 72.8 % | 82.3 % |
| acierto de ruta | — | 82.1 % | 79.2 % (5 dominios) |
| acierto de parámetros dada la ruta | 85.7 % | 82.7 % | 85.7 % |
| llamadas por turno (enrutamiento) | 1.00 (0) | 2.17 (1.21) | 1.92 (0.97) |
| costo por consulta correcta | $0.00180 | $0.00154 | **$0.00110** |
| turnos en legacy | 100 % | 36.1 % | **26.5 % (meta < 25 %: no cumplida)** |

El costo por consulta correcta, la métrica que manda, baja un 29 % frente a la mejor variante
modular y un 39 % frente a legacy. El acierto total queda 1.7 puntos por debajo de legacy medido en
la misma corrida (dentro del ruido entre corridas, pero en la misma dirección). En el piloto solo:
igual acierto que legacy (73.3 %), 21.1 % de turnos en legacy y −46 % de costo por consulta
correcta.

## Adoptado (implementado y probado con proveedores simulados)

| Tarea | Qué | Activo por defecto |
| --- | --- | --- |
| T4 | `ultima_nota` para clientes (`CREDITO_DATA`, `SUM(TOTAL)` por folio) | Sí (corrección) |
| T5 | Número solo elige fila de lista; código dictado por voz, coincidencia exacta | Sí (corrección) |
| T1, T2 | Métricas separadas en el evaluador; calibración sin API | Herramientas |
| T6 | Pre-enrutador determinista | Con `[modular]` |
| T7 | Aclaraciones del servicio resueltas sin modelo | Con `[modular]` |
| T8 | Estado conservado tras legacy verificado | Con `[modular]` |
| T9 | Respaldo de extracción con motivo; sin segundo cliente con el mismo perfil | Con `[modular]` |
| T10–T12 | Módulo de dominio, decisión por niveles, enrutador con estado | `min_margin`, `min_domain_confidence` |
| T14–T16 | `core.md` partido, `system.md` generado (`catalog-domains-v7`), texto por operación, `otra_operacion` | Prompt completo regenerado; resto con `[modular]` |
| T20 | Historial reducido | Bandera `compact_history` |
| T21–T23 | Dominios `proveedores`, `bancos`, `productos` | Añadirlos a `domains` |

## Medido

- **T13** umbrales calibrados en desarrollo: `min_confidence = 0.8`, `min_margin = 0.2`,
  `min_domain_confidence = 0.4`. Verificación única en el piloto aceptada (ruta 73.8 % ≥ 71.4 %;
  1.00 llamadas de enrutamiento por turno). Calibrados para estos conjuntos, no garantía de
  producción (`docs/evaluations/thresholds/summary.md`).
- **T18** `stable_prefix`: **descartado**. La caché sube (+518 tokens por turno), pero el prompt
  crece más y el costo por consulta correcta sube un 18.8 %.
- **T19** `fixed_tool_schema`: **descartado**. El costo no cambia (−0.4 %) y el acierto de
  parámetros baja 1.0 punto.
- **T17** ejemplos por operación: el acierto de parámetros dada la ruta no baja (88.5 % frente a
  86.0 % en los conjuntos compartidos), pero la línea base ya contenía los ejemplos, así que su
  efecto no está aislado. Tres casos empeoraron (`homonym-balances`,
  `last-payment-keeps-chosen-code`, `global-customer-count`); no se retiró ningún ejemplo
  (`docs/evaluations/experiments/summary.md`).

## Pendiente

- **T17:** decidir si se retiran los ejemplos de `saldos_coincidencias` y `ultimo_pago`; para
  aislar su efecto habría que medir el código anterior al commit de T17.
- **`legacy_share` < 25 %:** no se alcanza en los tres conjuntos (26.5 %); el 75 % de los turnos de
  aclaración va a legacy.
- **Por datos:** T24 — no hay redacciones reales nuevas del dueño en el repositorio.
- **Decisiones del dueño (no implementadas):**
  1. ¿El reporte de banco sin fechas debe pedirlas, como el estado de cuenta de personas?
  2. ¿«Saldo de Enrique» tras consultar su banco debe ofrecer también la cuenta bancaria homónima?
  3. ¿Se mantiene el vencimiento de memoria de 30 minutos?

## Notas para revisión

- T4 y T5 cambian el comportamiento también fuera de `[modular]`: son las correcciones del bloque B
  (errores del piloto) y el plan no les asigna bandera.
- T6: la traza conserva `modular_fallback = "route_inconsistent"` (lo exige una prueba existente) y
  añade `local_decision = "pre_route_legacy"`.
- T14: con aprobación del dueño, la prueba que exigía «homónimos» en todas las rutas ahora lo exige
  solo en las de personas.
- T15: el prompt completo incorpora las reglas de gastos y de `estado_cuenta` que antes solo tenía
  el modular; los servidores con `config/prompts/` propios no cambian.
- Corrección colateral: `templates/shared/*.md` faltaba en `package-data` desde T14 (resuelto en T15).
- Mediciones (2026-11-12): en un clon limpio, `tests/unit/data/core_before_split.md` no existe (lo
  ignora `.gitignore:102`, `data/`), así que `test_split_core_moves_text_without_losing_rules`
  falla. Para la puerta de pruebas se regeneró solo en local desde `b18b9f0^`; conviene versionarlo
  con una excepción en `.gitignore`.
- Mediciones: T13 añadió una corrida de observación con 5 dominios en desarrollo
  (`thresholds/development-all-domains`), porque las probabilidades de Jev dependen del conjunto de
  rutas registrado.
