import importlib
import subprocess
import sys
import unittest
from pathlib import Path


class EntrypointTests(unittest.TestCase):
    def test_legacy_webhook_is_the_canonical_factory(self):
        legacy = importlib.import_module("whatsapp_agent")
        modern = importlib.import_module("dbagg.api.app")
        self.assertIs(legacy.create_app, modern.create_app)

    def test_report_help_and_context_validation_need_no_credentials(self):
        root = Path(__file__).resolve().parents[2]
        for args in (
            ["score_riesgo.py", "--help"],
            ["business_context.py"],
            ["-m", "dbagg.evaluation", "--help"],
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, *args], cwd=root, capture_output=True, text=True, timeout=15
                )
                self.assertEqual(result.returncode, 0, result.stderr)
