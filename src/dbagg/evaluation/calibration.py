"""Calibrate Jev thresholds from a saved evaluation report; never calls an API.

Reads the per-case decisions written by ``dbagg.evaluation.modular`` (route expected, route
chosen by the router and its probabilities) and produces a confusion matrix, coverage/accuracy
curves by confidence and by margin, and accuracy at domain level.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

THRESHOLDS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))


def route_domain(route):
    return "legacy" if not route or route == "legacy" else route.split(".", 1)[0]


def decisions(report, variant=None):
    """Scored routing decisions: one per case run with an expected route and probabilities."""
    found = []
    for name, data in report.get("variants", {}).items():
        if variant and name != variant:
            continue
        for result in data.get("results", []):
            expected = result.get("route_expected")
            probabilities = result.get("probabilities") or {}
            chosen = result.get("routing_route") or (
                max(probabilities, key=probabilities.get) if probabilities else None
            )
            if expected is None or chosen is None:
                continue
            found.append(
                {
                    "variant": name,
                    "case": result.get("case"),
                    "expected": expected,
                    "chosen": chosen,
                    "probabilities": probabilities,
                    "confidence": result.get("confidence", probabilities.get(chosen)),
                }
            )
    return found


def margin(probabilities):
    ordered = sorted(probabilities.values(), reverse=True)
    if not ordered:
        return None
    return ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)


def confusion(items):
    matrix = defaultdict(lambda: defaultdict(int))
    for item in items:
        matrix[item["expected"]][item["chosen"]] += 1
    return {expected: dict(sorted(row.items())) for expected, row in sorted(matrix.items())}


def curve(items, score, thresholds=THRESHOLDS):
    """For each threshold: share of decisions kept (coverage) and accuracy among them."""
    total = len(items)
    points = []
    for threshold in thresholds:
        kept = [i for i in items if score(i) is not None and score(i) >= threshold - 1e-12]
        correct = sum(i["chosen"] == i["expected"] for i in kept)
        points.append(
            {
                "threshold": threshold,
                "covered": len(kept),
                "coverage": len(kept) / total if total else None,
                "accuracy": correct / len(kept) if kept else None,
            }
        )
    return points


def domain_accuracy(items):
    """Accuracy when only the domain matters: the domain with the largest summed probability."""
    scored = 0
    correct = 0
    for item in items:
        sums = defaultdict(float)
        for route, probability in item["probabilities"].items():
            sums[route_domain(route)] += probability
        if not sums:
            continue
        scored += 1
        correct += max(sums, key=sums.get) == route_domain(item["expected"])
    return {"scored": scored, "accuracy": correct / scored if scored else None}


def calibrate(report, variant=None):
    items = decisions(report, variant)
    with_probabilities = [i for i in items if i["probabilities"]]
    return {
        "decisions": len(items),
        "route_accuracy": (
            sum(i["chosen"] == i["expected"] for i in items) / len(items) if items else None
        ),
        "confusion": confusion(items),
        "confidence_curve": curve(with_probabilities, lambda i: i["confidence"]),
        "margin_curve": curve(with_probabilities, lambda i: margin(i["probabilities"])),
        "domain": domain_accuracy(with_probabilities),
    }


def _cell(value, percent=True):
    if value is None:
        return "—"
    return f"{value:.1%}" if percent else str(value)


def render_text(result):
    lines = [
        f"Decisiones: {result['decisions']}  Acierto de ruta: {_cell(result['route_accuracy'])}",
        f"Acierto de dominio: {_cell(result['domain']['accuracy'])} "
        f"({result['domain']['scored']} con probabilidades)",
        "",
        "Umbral  Cobertura(conf)  Acierto(conf)  Cobertura(margen)  Acierto(margen)",
    ]
    for by_confidence, by_margin in zip(result["confidence_curve"], result["margin_curve"]):
        lines.append(
            f"{by_confidence['threshold']:.2f}    {_cell(by_confidence['coverage']):>15}  "
            f"{_cell(by_confidence['accuracy']):>13}  {_cell(by_margin['coverage']):>17}  "
            f"{_cell(by_margin['accuracy']):>15}"
        )
    lines += ["", "Matriz de confusión (esperada → elegida: casos)"]
    for expected, row in result["confusion"].items():
        cells = ", ".join(f"{chosen}: {count}" for chosen, count in row.items())
        lines.append(f"{expected} → {cells}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="benchmark.json del evaluador")
    parser.add_argument("--variant", help="Solo esta variante; por defecto todas")
    parser.add_argument("--output", type=Path, help="Guardar el JSON aquí")
    args = parser.parse_args(argv)
    report = json.loads(args.input.read_text(encoding="utf-8"))
    result = calibrate(report, args.variant)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
