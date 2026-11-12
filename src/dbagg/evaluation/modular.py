"""Compare real model decisions on synthetic cases without database access.

This measures routing/parameters and API usage, not business-answer accuracy.
The probe runs the actual Assistant flow up to the first executable tool.
"""

import argparse
import html
import json
import math
import os
import time
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dbagg.agent.service import Assistant, code_confirmed
from dbagg.business.banks import BANK_OPERATIONS
from dbagg.business.products import PRODUCT_OPERATIONS
from dbagg.business.suppliers import SUPPLIER_ONLY_OPERATIONS
from dbagg.database.catalog import load_catalog
from dbagg.evaluation.cost import estimate_events
from dbagg.evaluation.routing import _NoDatabase, matches
from dbagg.llm.config import LLMConfig, ModelProfile, ModularConfig
from dbagg.routing.registry import available_routes


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


def expected_route(case, routes):
    """The registered route that should handle a case, «legacy», or None when any route may.

    Clarifications can come from any route, so they are not scored for routing.
    """
    tool = case.get("expected_tool")
    arguments = case.get("expected_arguments", {})
    if tool == "consultar_negocio":
        operation = arguments.get("operation")
        names = [n for n, r in routes.items() if r.operation == operation]
        if len(names) > 1:
            supplier = (
                arguments.get("entidad") == "proveedor" or case.get("domain") == "proveedores"
            )
            domain = "proveedores" if supplier else "clientes"
            names = [n for n in names if routes[n].domain == domain]
        return names[0] if len(names) == 1 else "legacy"
    if tool in {"describir_tablas", "consultar_sql"}:
        for route in routes.values():
            if route.executor != "exploration":
                continue
            tables = {t.lower() for t in arguments.get("tables", [])}
            if tables and tables <= {t.lower() for t in route.tables}:
                return route.name
        return "legacy"
    return None


def case_domain(case, catalog=None):
    """The case's declared domain, or one derived from its expected decision."""
    if case.get("domain"):
        return case["domain"]
    tool = case.get("expected_tool")
    arguments = case.get("expected_arguments", {})
    operation = arguments.get("operation")
    if tool == "consultar_negocio":
        if operation in BANK_OPERATIONS:
            return "bancos"
        if operation in PRODUCT_OPERATIONS:
            return "productos"
        if operation in SUPPLIER_ONLY_OPERATIONS or arguments.get("entidad") == "proveedor":
            return "proveedores"
        return "clientes"
    if tool in {"describir_tablas", "consultar_sql"}:
        domains = set()
        for table in arguments.get("tables", []):
            source = catalog.sources.get(table.lower()) if catalog else None
            domains.add(source.domain if source else "exploracion")
        return domains.pop() if len(domains) == 1 else "exploracion"
    return "aclaracion"


def _usage_numbers(usage):
    if not isinstance(usage, dict):
        return None
    incoming = usage.get("prompt_tokens", usage.get("input_tokens"))
    outgoing = usage.get("completion_tokens", usage.get("output_tokens"))
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    if type(incoming) is not int or type(outgoing) is not int or type(cached) is not int:
        return None
    return incoming, outgoing, cached


def call_metrics(events):
    """Model calls per task and their tokens; unknown usage is counted, never zero."""
    usage = [e for e in events if e.get("event") == "usage"]
    tokens = {"input": 0, "output": 0, "cached": 0, "unknown_calls": 0}
    for event in usage:
        numbers = _usage_numbers(event.get("usage"))
        if numbers is None:
            tokens["unknown_calls"] += 1
            continue
        tokens["input"] += numbers[0]
        tokens["output"] += numbers[1]
        tokens["cached"] += numbers[2]
    return dict(Counter(e.get("task", "unknown") for e in usage)), tokens


def evaluate(assistant, case, today, routes=None):
    tool, arguments = None, {}
    error = None
    started = time.monotonic()
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
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    trace = assistant.last_trace
    events = trace.get("events", [])
    passed = error is None and matches(case, tool, arguments)
    modular = assistant.llm_config.modular
    if routes is None:
        routes = (
            available_routes(assistant.catalog, assistant.tables, modular.domains)
            if modular.enabled
            else {}
        )
    route_expected = expected_route(case, routes)
    route_selected = trace.get("route") or "legacy"
    candidates = trace.get("route_candidates") or [route_selected]
    route_correct = (
        None
        if not modular.enabled or modular.shadow or route_expected is None
        else route_expected in candidates
    )
    arguments_correct = passed if route_correct or route_selected == "legacy" else None
    calls, tokens = call_metrics(events)
    routing = next((e for e in events if e.get("event") == "routing" and not e.get("fallback")), {})
    return {
        "case": case["id"],
        "domain": case_domain(case, assistant.catalog),
        "passed": passed,
        "tool": tool,
        "arguments": arguments,
        "error": error,
        "route": trace.get("route"),
        "route_expected": route_expected,
        "route_selected": route_selected,
        "route_correct": route_correct,
        "route_candidates": trace.get("route_candidates"),
        "routing_level": trace.get("routing_level"),
        "arguments_correct": arguments_correct,
        "routing_provider": routing.get("provider"),
        "routing_route": routing.get("route"),
        "confidence": routing.get("confidence"),
        "probabilities": routing.get("probabilities") or {},
        "local_decision": trace.get("local_decision"),
        "calls": calls,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "events": events,
        "cost": estimate_events(events),
    }


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _ratio(numerator, denominator):
    return None if not denominator else numerator / denominator


def summarize(results):
    """Route accuracy, parameter accuracy given the route, calls, tokens and cost per correct."""
    runs = len(results)
    routed = [r for r in results if r.get("route_correct") is not None]
    argued = [r for r in results if r.get("arguments_correct") is not None]
    passed = sum(bool(r["passed"]) for r in results)
    events = [e for r in results for e in r.get("events", [])]
    cost = estimate_events(events)
    calls = Counter()
    tokens = Counter()
    for result in results:
        calls.update(result.get("calls", {}))
        tokens.update(result.get("tokens", {}))
    durations = [r["duration_ms"] for r in results if r.get("duration_ms") is not None]
    routing_calls = calls.get("routing", 0)
    return {
        "runs": runs,
        "passed": passed,
        "accuracy": _ratio(passed, runs),
        "route_accuracy": _ratio(sum(r["route_correct"] for r in routed), len(routed)),
        "route_scored": len(routed),
        "arguments_accuracy_given_route": _ratio(
            sum(r["arguments_correct"] for r in argued), len(argued)
        ),
        "arguments_scored": len(argued),
        "legacy_share": _ratio(sum(r.get("route_selected") == "legacy" for r in results), runs),
        "calls_per_turn": _ratio(sum(calls.values()), runs),
        "routing_calls_per_turn": _ratio(routing_calls, runs),
        "calls_by_task": dict(calls),
        "tokens_per_turn": {
            key: _ratio(tokens.get(key, 0), runs) for key in ("input", "output", "cached")
        },
        "unknown_usage_calls": tokens.get("unknown_calls", 0),
        "cost": cost,
        # Unknown usage makes the known cost a lower bound; the flag travels with the figure.
        "cost_per_correct_usd": _ratio(cost["usd_known"], passed) if passed else None,
        "cost_per_correct_complete": cost["complete"],
        "latency_ms": {"p50": _percentile(durations, 0.5), "p95": _percentile(durations, 0.95)},
    }


def summarize_by_domain(results):
    domains = sorted({r.get("domain") or "sin_dominio" for r in results})
    return {
        domain: summarize([r for r in results if (r.get("domain") or "sin_dominio") == domain])
        for domain in domains
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


def _fmt(value, kind="ratio"):
    if value is None:
        return "—"
    if kind == "ratio":
        return f"{value:.1%}"
    if kind == "usd":
        return f"{value:.6f}"
    return f"{value:.2f}"


def _metric_row(name, summary):
    return (
        f"<tr><td>{html.escape(name)}</td>"
        f"<td>{summary['passed']}/{summary['runs']}</td>"
        f"<td>{_fmt(summary['route_accuracy'])} ({summary['route_scored']})</td>"
        f"<td>{_fmt(summary['arguments_accuracy_given_route'])} ({summary['arguments_scored']})</td>"
        f"<td>{_fmt(summary['calls_per_turn'], 'n')}</td>"
        f"<td>{_fmt(summary['tokens_per_turn']['input'], 'n')} / "
        f"{_fmt(summary['tokens_per_turn']['cached'], 'n')} / "
        f"{_fmt(summary['tokens_per_turn']['output'], 'n')}</td>"
        f"<td>{_fmt(summary['cost']['usd_known'], 'usd')}</td>"
        f"<td>{_fmt(summary['cost_per_correct_usd'], 'usd')}"
        f"{'' if summary['cost_per_correct_complete'] else ' (incompleto)'}</td>"
        f"<td>{_fmt(summary['latency_ms']['p50'], 'n')} / {_fmt(summary['latency_ms']['p95'], 'n')}</td>"
        "</tr>"
    )


METRIC_HEADER = (
    "<tr><th>Grupo</th><th>Aciertos</th><th>Acierto de ruta (casos)</th>"
    "<th>Parámetros dada la ruta (casos)</th><th>Llamadas/turno</th>"
    "<th>Tokens/turno entrada/caché/salida</th><th>Costo conocido USD</th>"
    "<th>Costo por consulta correcta USD</th><th>Latencia p50/p95 ms</th></tr>"
)


def render_report(report):
    rows = []
    sections = []
    for name, variant in report["variants"].items():
        rows.append(
            f"<tr><td>{html.escape(name)}</td><td>{variant['status']}</td><td>"
            f"{variant.get('passed', '—')}/{variant.get('runs', '—')}</td><td>"
            f"{html.escape(json.dumps(variant.get('cost', {})))}</td></tr>"
        )
        if variant.get("summary"):
            domain_rows = "".join(
                _metric_row(domain, summary)
                for domain, summary in variant.get("domains", {}).items()
            )
            sections.append(
                f"<h2>{html.escape(name)}</h2><table>{METRIC_HEADER}"
                + _metric_row("total", variant["summary"])
                + domain_rows
                + "</table>"
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
        + "".join(sections)
        + _chart(report)
        + "</html>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=[
            "legacy_mini",
            "reference_mini",
            "reference_nano",
            "jev_mini",
            "jev_nano",
            "custom",
        ],
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="LLM TOML local evaluado como variante «custom» (p. ej. todo activado, umbrales calibrados)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20:
        parser.error("repeat debe estar entre 1 y 20")
    datasets = [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in args.dataset]
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
    if args.config:
        from dbagg.llm.config import load_llm_config

        loaded = load_llm_config(environ={"LLM_CONFIG": str(args.config.resolve())})
        variants["custom"] = loaded.modular
        nano = loaded.profiles.get("nano", nano)
    report = {
        "scope": "Synthetic first-tool routing and parameters; no database, no answer accuracy.",
        "jev_threshold": "0.8 is evaluation-only, uncalibrated; not a production recommendation.",
        "datasets": {name: data.get("business_today") for name, data in datasets},
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
        routes = (
            available_routes(assistant.catalog, assistant.tables, modular.domains)
            if modular.enabled
            else {}
        )
        results = []
        unavailable = False
        try:
            for repetition in range(args.repeat):
                for case, dataset_name, today in (
                    (case, name, date.fromisoformat(data["business_today"]))
                    for name, data in datasets
                    for case in data["cases"]
                    if "expected_tool" in case
                ):
                    result = evaluate(assistant, case, today, routes)
                    result["dataset"] = dataset_name
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
            "summary": summarize(results),
            "domains": summarize_by_domain(results),
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
