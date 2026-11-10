"""Compare real model decisions on synthetic cases without database access.

This measures routing/parameters and API usage, not business-answer accuracy.
The probe runs the actual Assistant flow up to the first executable tool.
"""

import argparse
import html
import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbagg.agent.service import Assistant, code_confirmed
from dbagg.database.catalog import load_catalog
from dbagg.evaluation.cost import estimate_events
from dbagg.evaluation.routing import _NoDatabase, matches
from dbagg.llm.config import LLMConfig, ModelProfile, ModularConfig


class FirstDecision(Exception):
    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments


class ProbeAssistant(Assistant):
    def _consult_business(self, arguments, *args):
        raise FirstDecision("consultar_negocio", arguments)

    def _describe_tables(self, selected, described):
        allowed = getattr(self._local, "route_tables", None)
        allowed = self.tables if allowed is None else allowed
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= 5
            or any(not isinstance(t, str) or t.lower() not in allowed for t in selected)
        ):
            raise ValueError("Objetos no autorizados.")
        raise FirstDecision("describir_tablas", {"tables": selected})

    def _query_sql(self, sql, described):
        # Run the actual SQL validator, including describe-before-query. The no-DB
        # sentinel prevents execution even if a valid SQL slips through this probe.
        return super()._query_sql(sql, described)


def evaluate(assistant, case, today):
    tool, arguments = None, {}
    error = None
    try:
        with patch("dbagg.agent.service.business_today", return_value=today):
            assistant.answer(case["question"], history=case.get("history"))
        clarification = assistant.last_trace.get("clarification")
        if clarification:
            tool, arguments = "pedir_aclaracion", {"reason": clarification}
    except FirstDecision as decision:
        tool, arguments = decision.tool, dict(decision.arguments)
        if "codigo_exacto" in arguments:
            arguments["codigo_exacto"] = arguments["codigo_exacto"] is True and code_confirmed(
                arguments.get("customer"), case["question"], case.get("history")
            )
    except Exception as exc:
        error = type(exc).__name__
    trace = assistant.last_trace
    events = trace.get("events", [])
    return {
        "case": case["id"],
        "passed": error is None and matches(case, tool, arguments),
        "tool": tool,
        "arguments": arguments,
        "error": error,
        "route": trace.get("route"),
        "events": events,
        "cost": estimate_events(events),
    }


def _chart(report):
    measured = [
        (name, variant)
        for name, variant in report["variants"].items()
        if variant.get("status") == "measured"
    ]
    bars = []
    for index, (name, variant) in enumerate(measured):
        y = 40 + index * 55
        score = variant["passed"] / variant["runs"]
        label = html.escape(name)
        bars.append(
            f'<text x="0" y="{y + 18}">{label}</text>'
            f'<rect x="190" y="{y}" width="{score * 400}" height="28" fill="#247a6b"/>'
            f'<text x="610" y="{y + 18}">{score:.1%}</text>'
        )
    return (
        "<h2>Exactitud de primera decisión</h2><p>Muestra de desarrollo; no mide respuestas financieras.</p>"
        f'<svg role="img" aria-label="Exactitud por variante" viewBox="0 0 740 {60 + 55 * len(measured)}">'
        + "".join(bars)
        + "</svg>"
    )


def render_report(report):
    rows = []
    for name, variant in report["variants"].items():
        rows.append(
            f"<tr><td>{html.escape(name)}</td><td>{variant['status']}</td><td>"
            f"{variant.get('passed', '—')}/{variant.get('runs', '—')}</td><td>"
            f"{html.escape(json.dumps(variant.get('cost', {})))}</td></tr>"
        )
    return (
        '<!doctype html><html lang="es"><meta charset="utf-8"><title>Evaluación modular</title>'
        "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto}td,th{padding:12px;text-align:left}table{border-collapse:collapse}tr{border-bottom:1px solid #ccc}</style>"
        "<h1>Evaluación modular con APIs reales</h1><p>Casos sintéticos. Evalúa ruta y parámetros; "
        "no ejecuta SQL ni mide exactitud financiera. Los costos son estimaciones de tarifa pública, "
        "no factura. El consumo desconocido se conserva como desconocido.</p>"
        "<table><tr><th>Variante</th><th>Estado</th><th>Aciertos</th><th>Costo USD</th></tr>"
        + "".join(rows)
        + "</table>"
        + _chart(report)
        + "</html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["legacy_mini", "reference_mini", "reference_nano", "jev_mini", "jev_nano"],
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20:
        parser.error("repeat debe estar entre 1 y 20")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    profile = ModelProfile(temperature=0, max_retries=0)
    nano = replace(profile, name="nano", model="gpt-4.1-nano")
    variants = {
        "legacy_mini": ModularConfig(),
        "reference_mini": ModularConfig(enabled=True),
        "reference_nano": ModularConfig(enabled=True, extraction_profile="nano"),
        "jev_mini": ModularConfig(enabled=True, router="jev", min_confidence=0.8),
        "jev_nano": ModularConfig(
            enabled=True, router="jev", min_confidence=0.8, extraction_profile="nano"
        ),
    }
    report = {
        "scope": "Synthetic first-tool routing and parameters; no database, no answer accuracy.",
        "jev_threshold": "0.8 is evaluation-only, uncalibrated; not a production recommendation.",
        "business_today": dataset["business_today"],
        "repeat": args.repeat,
        "variants": {},
    }
    for name, modular in variants.items():
        if args.variants and name not in args.variants:
            continue
        if modular.router == "jev" and not os.getenv("TYPESAFE_API_KEY"):
            report["variants"][name] = {
                "status": "unavailable",
                "reason": "TYPESAFE_API_KEY missing",
            }
            continue
        config = LLMConfig(profile, modular=modular, profiles={"nano": nano})
        settings = SimpleNamespace(
            model=profile.model,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            tables=frozenset(load_catalog().sources),
            llm=config,
        )
        assistant = ProbeAssistant(settings, db=_NoDatabase())
        results = []
        unavailable = False
        try:
            for repetition in range(args.repeat):
                for case in dataset["cases"]:
                    result = evaluate(
                        assistant, case, date.fromisoformat(dataset["business_today"])
                    )
                    result["repetition"] = repetition + 1
                    results.append(result)
                    print(
                        f"{name}: {case['id']} {'PASS' if result['passed'] else 'FAIL'}", flush=True
                    )
                    if any(
                        e.get("event") == "fallback"
                        and e.get("task") == "extraction"
                        and e.get("reason")
                        in {"NotFoundError", "AuthenticationError", "PermissionDeniedError"}
                        for e in result["events"]
                    ):
                        unavailable = True
                        break
                if unavailable:
                    break
        finally:
            assistant.close()
        events = [event for result in results for event in result["events"]]
        report["variants"][name] = {
            "status": "unavailable" if unavailable else "measured",
            "passed": sum(r["passed"] for r in results),
            "runs": len(results),
            "cost": estimate_events(events),
            "results": results,
        }
        if unavailable:
            report["variants"][name]["reason"] = (
                "Extraction API unavailable; bounded fallback observed, remaining cases not attempted."
            )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "benchmark.html").write_text(render_report(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
