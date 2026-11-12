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
