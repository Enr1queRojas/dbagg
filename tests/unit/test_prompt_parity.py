"""system.md is generated from the modules; editing one without the other must fail."""

import unittest

from dbagg.agent.prompt_files import (
    OPERATION_MODULES,
    TEMPLATES,
    compose_system_prompt,
    load_prompt_bundle,
)
from dbagg.business.customers import OPERATIONS
from dbagg.business.statements import STATEMENT_OPERATION


class PromptParityTests(unittest.TestCase):
    def test_system_prompt_on_disk_is_the_composition_of_its_modules(self):
        on_disk = (TEMPLATES / "system.md").read_text(encoding="utf-8")
        self.assertEqual(
            on_disk,
            compose_system_prompt(),
            "Ejecuta: python scripts/build_system_prompt.py",
        )

    def test_every_customer_operation_has_a_module_in_the_composition(self):
        self.assertEqual(set(OPERATION_MODULES), set(OPERATIONS) | {STATEMENT_OPERATION})
        system = compose_system_prompt()
        for name in ("# Bancos", "# Productos", "# Gastos", "# Ejemplos de elección"):
            self.assertIn(name, system)
        self.assertNotIn("# Operación seleccionada", system)

    def test_bundle_still_respects_the_size_limit(self):
        bundle = load_prompt_bundle()
        self.assertLessEqual(len(bundle.system), 26000)
        self.assertTrue(bundle.version.startswith("catalog-domains-v7:"))


if __name__ == "__main__":
    unittest.main()


class OperationExampleTests(unittest.TestCase):
    def test_each_operation_has_fictitious_examples_only_in_modular_prompts(self):
        import json
        from pathlib import Path

        pilot = json.loads(
            (Path(__file__).resolve().parents[2] / "evals/cases/whatsapp-pilot.json").read_text(
                encoding="utf-8"
            )
        )
        questions = " ".join(c["question"] for c in pilot["cases"]).casefold()
        system = (TEMPLATES / "system.md").read_text(encoding="utf-8")
        for name in OPERATION_MODULES:
            with self.subTest(operation=name):
                text = (TEMPLATES / "operations" / f"{name}.md").read_text(encoding="utf-8")
                examples = [line for line in text.splitlines() if line.startswith("Ejemplo (")]
                self.assertTrue(1 <= len(examples) <= 2)
                self.assertTrue(all(line.startswith(f"Ejemplo ({name}):") for line in examples))
                for line in examples:
                    self.assertNotIn(line, system)
                    quoted = line.split("«", 1)[1].split("»", 1)[0].casefold()
                    self.assertNotIn(quoted, questions)
