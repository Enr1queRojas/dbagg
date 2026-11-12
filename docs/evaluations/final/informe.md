# Informe final — prompts y enrutamiento con Jev (2026-11-12)

Rama `claude/blissful-mayer-xgcf54`. Suite: 623 pruebas al inicio (T0) → 707 al cierre, todas en
verde; `ruff check` y `ruff format --check` limpios. Ninguna medición con API se ejecutó: no había
`OPENAI_API_KEY` ni `TYPESAFE_API_KEY` y la red de la sesión rechazaba las APIs. No hay cifras
simuladas.

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

## Implementado pero sin decidir (requiere medición)

- **T17** ejemplos por operación (solo modular): retirar el que empeore un caso.
- **T18** `stable_prefix` y **T19** `fixed_tool_schema`: adoptar solo si baja el costo por consulta
  correcta sin perder acierto.

## Pendiente

- **Por claves/red:** T3 (línea base), T13 (umbrales), medición de T17–T19, T25 (evaluación final).
  Comandos en `docs/evaluations/{pilot-baseline,thresholds,final}/PENDIENTE.md`.
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
