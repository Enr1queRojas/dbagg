"""Calibration figures from a fixed list of synthetic decisions, checked by hand."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from dbagg.evaluation.calibration import calibrate, main, margin, route_domain


def result(case, expected, chosen, probabilities):
    return {
        "case": case,
        "route_expected": expected,
        "routing_route": chosen,
        "confidence": probabilities.get(chosen),
        "probabilities": probabilities,
    }


REPORT = {
    "variants": {
        "jev_mini": {
            "results": [
                # Right, confident, wide margin (0.9 - 0.05 = 0.85).
                result(
                    "a",
                    "clientes.saldo",
                    "clientes.saldo",
                    {"clientes.saldo": 0.9, "clientes.pagos": 0.05, "legacy": 0.05},
                ),
                # Right but unsure (0.6 - 0.3 = 0.3).
                result(
                    "b",
                    "clientes.pagos",
                    "clientes.pagos",
                    {"clientes.pagos": 0.6, "clientes.total_pagos": 0.3, "legacy": 0.1},
                ),
                # Wrong route, right domain (0.55 - 0.45 = 0.1).
                result(
                    "c",
                    "clientes.total_pagos",
                    "clientes.pagos",
                    {"clientes.pagos": 0.55, "clientes.total_pagos": 0.45},
                ),
                # Wrong domain, confident (0.8 - 0.2 = 0.6).
                result(
                    "d", "legacy", "gastos.exploracion", {"gastos.exploracion": 0.8, "legacy": 0.2}
                ),
                # Not scored: clarification has no expected route.
                result("e", None, "clientes.saldo", {"clientes.saldo": 1.0}),
            ]
        },
        "legacy_mini": {"results": [result("a", "clientes.saldo", None, {})]},
    }
}


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.result = calibrate(REPORT)

    def point(self, curve, threshold):
        return next(p for p in self.result[curve] if p["threshold"] == threshold)

    def test_confusion_matrix(self):
        self.assertEqual(self.result["decisions"], 4)
        self.assertEqual(self.result["route_accuracy"], 0.5)
        self.assertEqual(
            self.result["confusion"],
            {
                "clientes.pagos": {"clientes.pagos": 1},
                "clientes.saldo": {"clientes.saldo": 1},
                "clientes.total_pagos": {"clientes.pagos": 1},
                "legacy": {"gastos.exploracion": 1},
            },
        )

    def test_confidence_curve(self):
        # Confidences: 0.9 ok, 0.6 ok, 0.55 bad, 0.8 bad.
        self.assertEqual(self.point("confidence_curve", 0.5)["coverage"], 1.0)
        self.assertEqual(self.point("confidence_curve", 0.5)["accuracy"], 0.5)
        at_60 = self.point("confidence_curve", 0.6)
        self.assertEqual((at_60["covered"], at_60["accuracy"]), (3, 2 / 3))
        at_85 = self.point("confidence_curve", 0.85)
        self.assertEqual((at_85["covered"], at_85["accuracy"]), (1, 1.0))
        at_95 = self.point("confidence_curve", 0.95)
        self.assertEqual((at_95["coverage"], at_95["accuracy"]), (0.0, None))

    def test_margin_curve(self):
        self.assertAlmostEqual(margin({"a": 0.6, "b": 0.3, "c": 0.1}), 0.3)
        self.assertEqual(margin({"a": 1.0}), 1.0)
        # Margins: 0.85 ok, 0.3 ok, 0.1 bad, 0.6 bad; at 0.5 only 0.85 and 0.6 remain.
        at_50 = self.point("margin_curve", 0.5)
        self.assertEqual((at_50["covered"], at_50["accuracy"]), (2, 0.5))
        at_65 = self.point("margin_curve", 0.65)
        self.assertEqual((at_65["covered"], at_65["accuracy"]), (1, 1.0))

    def test_domain_accuracy_sums_probabilities_of_each_domain(self):
        # a, b, c pick «clientes» (right); d picks «gastos» against «legacy» (wrong).
        self.assertEqual(self.result["domain"], {"scored": 4, "accuracy": 0.75})
        self.assertEqual(route_domain("legacy"), "legacy")
        self.assertEqual(route_domain("clientes.saldo"), "clientes")

    def test_variant_filter(self):
        self.assertEqual(calibrate(REPORT, "legacy_mini")["decisions"], 0)

    def test_command_reads_a_report_without_any_api(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "report.json"
            source.write_text(json.dumps(REPORT), encoding="utf-8")
            output = Path(folder) / "calibration.json"
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["decisions"], 4)
        self.assertIn("Matriz de confusión", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
