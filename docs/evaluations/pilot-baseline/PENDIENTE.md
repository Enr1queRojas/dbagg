# Línea base del piloto (T3) — pendiente

No se ejecutó: en la sesión del 2026-11-12 no había `OPENAI_API_KEY` ni `TYPESAFE_API_KEY`, y la
red de la sesión rechazaba `api.openai.com`. No hay resultados simulados en esta carpeta.

## Comandos exactos

Desde la raíz del repositorio, con ambas claves en el entorno:

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/pilot-baseline --repeat 3 --variants legacy_mini reference_mini jev_mini
```

```bash
python -m dbagg.evaluation.calibration --input docs/evaluations/pilot-baseline/benchmark.json --variant jev_mini --output docs/evaluations/pilot-baseline/calibration.json
```

Después, escribir `summary.md` en esta carpeta con la tabla de `summary` y `domains` de cada
variante (`benchmark.html` ya la muestra) y la lista de casos fallidos por variante
(`results[].passed == false`). Los casos son ficticios; no copiar mensajes reales.

Sin `TYPESAFE_API_KEY` la variante `jev_mini` queda como `unavailable` y la calibración no tiene
decisiones que puntuar.
