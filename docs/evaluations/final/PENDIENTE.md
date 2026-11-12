# Evaluación final (T25) — pendiente

No se ejecutó: sin `OPENAI_API_KEY` ni `TYPESAFE_API_KEY` y sin salida a las APIs en la sesión del
2026-11-12. Depende además de T3 (línea base) y T13 (umbrales). No hay resultados simulados.

1. Copiar `llm.all.example.toml` y poner los umbrales calibrados en T13 y las banderas adoptadas en
   T18/T19.
2. Ejecutar (`custom` es esa configuración; las otras variantes son la línea base):

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/final --repeat 3 --variants legacy_mini reference_mini jev_mini custom --config docs/evaluations/final/llm.all.toml
```

```bash
python -m dbagg.evaluation.calibration --input docs/evaluations/final/benchmark.json --variant custom --output docs/evaluations/final/calibration.json
```

3. Tabla comparativa contra `docs/evaluations/pilot-baseline/`: `route_accuracy`,
   `arguments_accuracy_given_route`, `calls_per_turn`, `tokens_per_turn`, `cost_per_correct_usd` y
   `legacy_share` (meta < 25 %), por variante y por dominio.
