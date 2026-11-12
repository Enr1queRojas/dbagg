# Calibración de umbrales (T13) — pendiente

No se ejecutó: sin `OPENAI_API_KEY` ni `TYPESAFE_API_KEY` y sin salida a las APIs en la sesión del
2026-11-12. No hay valores calibrados; `config/llm.modular.example.toml` deja los campos comentados.

1. Medir T10–T12 en los conjuntos de **desarrollo** con Jev en modo de observación de
   probabilidades (`jev_mini` usa `min_confidence=0.8` solo como punto de partida):

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/modular-development.json --dataset evals/cases/intent-routing.json --dataset evals/cases/bank-routing.json --output docs/evaluations/thresholds/development --repeat 3 --variants legacy_mini reference_mini jev_mini
```

```bash
python -m dbagg.evaluation.calibration --input docs/evaluations/thresholds/development/benchmark.json --variant jev_mini --output docs/evaluations/thresholds/development/calibration.json
```

2. Elegir `min_confidence`, `min_margin` y `min_domain_confidence` con las curvas de
   `calibration.json` (cobertura/acierto por confianza y por margen; acierto por dominio).
3. Verificar **una sola vez** en el conjunto de aceptación, con esos valores en una variante
   `jev_mini` del evaluador (o un `LLM_CONFIG` local):

```bash
python -m dbagg.evaluation.modular --dataset evals/cases/whatsapp-pilot.json --output docs/evaluations/thresholds/acceptance --repeat 3 --variants legacy_mini reference_mini jev_mini
```

Aceptar si el acierto de ruta es ≥ línea base (T3) y `routing_calls_per_turn` ≤ 1.1. Documentar
los valores como calibrados para este conjunto, no como garantía de producción.
