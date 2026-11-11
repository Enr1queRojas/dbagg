"""Evaluate the configured pilot's first executable tool; no ERP or WhatsApp.

Uses synthetic bank/modular cases and real Jev/OpenAI APIs. Output is local
JSON/JSONL; unknown provider usage is never treated as free.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

def main():
    from scripts.run_local_jev import prepare
    from dbagg.evaluation.cost import estimate_events
    from dbagg.evaluation.modular import ProbeAssistant, evaluate
    from dbagg.evaluation.routing import _NoDatabase

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["DBAGG_USAGE_PATH"] = str(args.output.resolve() / "usage.jsonl")
    settings, summary = prepare()
    assistant = ProbeAssistant(settings, db=_NoDatabase())
    results = []
    try:
        for filename in ("bank-routing.json", "modular-development.json"):
            dataset = json.loads((ROOT / "evals/cases" / filename).read_text(encoding="utf-8"))
            cases = list(dataset["cases"])
            if filename == "bank-routing.json":
                for question in ("dame un reporte de este mes", "dame el reporte de este mes{", "de este mes{"):
                    cases.append({
                        "id": "whatsapp-followup-" + str(len(cases)),
                        "question": question,
                        "history": [{"role": "assistant", "content":
                            "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\n"
                            + ("¿De qué fechas quieres el reporte?" if question.startswith("de ")
                               else "Saldo actual: -20.00.\nImportes en pesos.")}],
                        "expected_tool": "consultar_negocio",
                        "expected_arguments": {"operation": "movimientos_banco", "banco": "ENRIQUE", "period": "este_mes"},
                    })
            for case in cases:
                result = evaluate(assistant, case, date.fromisoformat(dataset["business_today"]))
                result["dataset"] = filename
                results.append(result)
                print(case["id"], "PASS" if result["passed"] else "FAIL", flush=True)
                report = {
                    "scope": "Synthetic first executable tool and parameters, no database execution or answer accuracy",
                    "configuration": summary,
                    "passed": sum(r["passed"] for r in results), "runs": len(results),
                    "results": results,
                }
                (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        assistant.close()
    report["cost"] = estimate_events([
        e for r in results for e in r["events"] if e.get("event") == "usage"
    ])
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Passed {report['passed']}/{report['runs']}")
    return 0 if report["passed"] == report["runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
