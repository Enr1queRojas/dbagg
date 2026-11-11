"""Compare complete synthetic conversations, counting every observed provider call.

Run locally: python scripts/compare_usage.py --output work/usage-compare-001
Loads keys from the checkout .env; never queries production SQL or sends WhatsApp.
"""

import argparse
import html
import json
import os
import subprocess
import sys
from pathlib import Path

# This checkout uses a src layout. Prefer its code to an older installed wheel.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from dbagg.evaluation.cost import estimate_event
from dbagg.paths import project_root


def totals(events):
    calls = [event for event in events if event["event"] == "usage"]
    result = {
        "calls": len(calls),
        "error_calls": sum(e["status"] == "error" for e in calls),
        "provider_duration_ms": round(sum(e["duration_ms"] for e in calls), 3),
    }
    for field in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"):
        values = [e.get(field) for e in calls]
        result["known_" + field] = sum(v for v in values if v is not None)
        result["unknown_" + field + "_calls"] = sum(v is None for v in values)
    costs = []
    for event in calls:
        raw = {
            "prompt_tokens": event.get("input_tokens"),
            "completion_tokens": event.get("output_tokens"),
            "prompt_tokens_details": {"cached_tokens": event.get("cached_input_tokens")},
        }
        costs.append(estimate_event(dict(event, usage=raw)))
    result["estimated_usd_known"] = sum(cost for cost in costs if cost is not None)
    result["unknown_cost_calls"] = sum(cost is None for cost in costs)
    result["providers"] = {
        provider: sum(e["provider"] == provider for e in calls)
        for provider in sorted({e["provider"] for e in calls})
    }
    return result


def render_report(report):
    badge = (
        "SIMULACIÓN: conteos artificiales"
        if report["mode"] == "mock"
        else "APIs reales: conteos informados por proveedor"
    )
    rows = []
    failures = []
    for variant in report["variants"]:
        data = variant["totals"]
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    variant["variant"],
                    variant["repetition"],
                    variant["status"],
                    variant["correct_answers"],
                    data["calls"],
                    data["known_input_tokens"],
                    data["known_output_tokens"],
                    data["known_cached_input_tokens"],
                    data["unknown_total_tokens_calls"],
                    f"{data['estimated_usd_known']:.8f}",
                    data["unknown_cost_calls"],
                    data["provider_duration_ms"],
                )
            )
            + "</tr>"
        )
        for failure in variant.get("failures", []):
            failures.append(
                f"<details><summary>{html.escape(variant['variant'])}: consulta {failure['id']}</summary>"
                f"<pre>{html.escape(json.dumps(failure, ensure_ascii=False, indent=2))}</pre></details>"
            )
    return (
        "<!doctype html><html lang='es'><meta charset='utf-8'><title>Comparación de usage</title>"
        "<style>body{font:15px system-ui;margin:32px}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style>"
        f"<h1>Flujo anterior vs contexto modular vs Jev</h1><p>{badge}</p>"
        "<p>Cuatro consultas con SQLite sintético por ejecución: saludo, saldo 100, pagos 25, gastos activos 42. "
        "Incluye clasificación, interpretación, respaldos y rondas SQL. Caché incluida en entrada; "
        "los campos conocidos son sumas parciales cuando hay uso desconocido. Jev no informa clase de caché. "
        "Costos estimados con tarifas del 2026-11-10, no factura. Una simulación no demuestra ahorro. "
        "Las etapas son inclusivas: no sumar sus tiempos anidados. El orden y la caché pueden afectar la comparación.</p>"
        "<table><tr><th>Variante</th><th>Repetición</th><th>Estado</th><th>Respuestas válidas / 4</th>"
        "<th>Llamadas</th><th>Entrada conocida</th><th>Salida conocida</th><th>Caché conocida</th>"
        "<th>Uso desconocido</th><th>USD conocidos</th><th>Costo desconocido</th><th>ms proveedores</th></tr>"
        + "".join(rows)
        + "</table><h2>Consultas con problemas</h2>"
        + ("".join(failures) or "<p>Ninguna.</p>")
        + "</html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["legacy", "reference", "jev"],
        default=["legacy", "reference", "jev"],
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Umbral experimental, no calibrado para producción.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20 or not 0 <= args.min_confidence <= 1:
        parser.error("repeat debe ser 1..20 y min-confidence 0..1")
    load_dotenv(project_root() / ".env")
    required = ["OPENAI_API_KEY"] + (["TYPESAFE_API_KEY"] if "jev" in args.variants else [])
    missing = [name for name in required if not os.getenv(name)]
    if not args.mock and missing:
        print("Faltan variables: " + ", ".join(missing) + ". No se hicieron llamadas.")
        return 2
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Usa un directorio nuevo para no mezclar ejecuciones")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "mock" if args.mock else "live",
        "database": "synthetic",
        "min_confidence": args.min_confidence,
        "threshold_calibrated": False,
        "variants": [],
    }
    script = Path(__file__).with_name("benchmark_usage.py").resolve()
    failed = False
    for repetition in range(1, args.repeat + 1):
        # Rotate the first variant across repetitions to limit systematic order bias.
        order = (
            args.variants[(repetition - 1) % len(args.variants) :]
            + args.variants[: (repetition - 1) % len(args.variants)]
        )
        for variant in order:
            folder = args.output / f"{variant}-{repetition}"
            command = [
                sys.executable,
                str(script),
                "--variant",
                variant,
                "--output",
                str(folder),
                "--min-confidence",
                str(args.min_confidence),
            ]
            if args.mock:
                command.append("--mock")
            completed = subprocess.run(command, check=False)
            failed |= completed.returncode != 0
            manifest_path = folder / "benchmark.json"
            if not manifest_path.exists():
                print(f"No se generó manifiesto para {variant}; código {completed.returncode}.")
                return 1
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            path = folder / "usage.jsonl"
            events = (
                [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                if path.exists()
                else []
            )
            result = {
                "variant": variant,
                "repetition": repetition,
                "status": manifest["status"],
                "correct_answers": sum(m.get("answer_valid") is True for m in manifest["messages"]),
                "totals": totals(events),
                "artifacts": folder.name,
                "failures": [
                    {
                        key: message.get(key)
                        for key in (
                            "id",
                            "question",
                            "answer",
                            "answer_status",
                            "route",
                            "modular_fallback",
                            "clarification",
                            "routing_events",
                            "fallbacks",
                            "error_calls",
                            "error_stages",
                        )
                    }
                    for message in manifest["messages"]
                    if not message.get("answer_valid")
                    or message.get("error_calls")
                    or message.get("error_stages")
                ],
            }
            report["variants"].append(result)
            (args.output / "comparison.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            (args.output / "comparison.html").write_text(render_report(report), encoding="utf-8")
            print(
                f"{variant}: {result['totals']['calls']} llamadas, {result['totals']['known_total_tokens']} tokens conocidos; {result['correct_answers']}/4 respuestas válidas.",
                flush=True,
            )
    print(f"Reporte: {args.output / 'comparison.html'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
