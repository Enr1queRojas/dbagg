"""CI guard for evals/cases: synthetic, unique and consistent with the current contracts."""

import json
import unittest
from pathlib import Path

from dbagg.agent.prompt_files import TOOL_NAMES
from dbagg.agent.service import CLARIFICATIONS
from dbagg.business.banks import BANK_OPERATIONS
from dbagg.business.customers import OPERATIONS
from dbagg.business.statements import STATEMENT_OPERATION
from dbagg.business.suppliers import SUPPLIER_ONLY_OPERATIONS
from dbagg.business.products import PRODUCT_OPERATIONS

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
                        self.assertIn(
                            arguments.get("operation"),
                            OPERATIONS
                            + (STATEMENT_OPERATION,)
                            + SUPPLIER_ONLY_OPERATIONS
                            + PRODUCT_OPERATIONS
                            + BANK_OPERATIONS,
                        )
                    if case["expected_tool"] == "pedir_aclaracion":
                        self.assertIn(arguments.get("reason"), CLARIFICATIONS)

    def test_routing_dataset_covers_every_operation_and_clarification(self):
        cases = dict(self.datasets())["intent-routing.json"]["cases"]
        arguments = [case.get("expected_arguments", {}) for case in cases]
        operations = {a.get("operation") for a in arguments}
        reasons = {a.get("reason") for a in arguments}
        self.assertEqual(set(OPERATIONS + PRODUCT_OPERATIONS) - operations, set())
        self.assertEqual(set(CLARIFICATIONS) - reasons, set())


if __name__ == "__main__":
    unittest.main()
