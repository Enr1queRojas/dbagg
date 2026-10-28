"""CI guard for evals/cases: synthetic, unique and consistent with the current contracts."""

import json
import unittest
from pathlib import Path

from dbagg.agent.prompt_files import TOOL_NAMES
from dbagg.agent.service import CLARIFICATIONS
from dbagg.business.customers import OPERATIONS

CASES = Path(__file__).resolve().parents[2] / "evals" / "cases"


class EvalCaseTests(unittest.TestCase):
    def datasets(self):
        files = sorted(CASES.glob("*.json"))
        self.assertTrue(files, "evals/cases no contiene datasets")
        return [(f.name, json.loads(f.read_text(encoding="utf-8"))) for f in files]

    def test_every_dataset_is_synthetic_with_unique_ids(self):
        seen = set()
        for name, dataset in self.datasets():
            with self.subTest(dataset=name):
                self.assertEqual(dataset.get("data_kind"), "synthetic")
                self.assertTrue(dataset.get("cases"))
                for case in dataset["cases"]:
                    self.assertTrue(case.get("id") and case.get("question"))
                    self.assertNotIn(case["id"], seen, f"id repetido: {case['id']}")
                    seen.add(case["id"])

    def test_routing_cases_reference_existing_tools_operations_and_reasons(self):
        for name, dataset in self.datasets():
            for case in dataset["cases"]:
                if "expected_tool" not in case:
                    continue
                with self.subTest(dataset=name, case=case["id"]):
                    self.assertIn(case["expected_tool"], TOOL_NAMES)
                    arguments = case.get("expected_arguments", {})
                    if case["expected_tool"] == "consultar_negocio":
                        self.assertIn(arguments.get("operation"), OPERATIONS)
                    if case["expected_tool"] == "pedir_aclaracion":
                        self.assertIn(arguments.get("reason"), CLARIFICATIONS)


if __name__ == "__main__":
    unittest.main()
