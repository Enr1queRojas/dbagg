"""Four complete conversations verify metering across history and SQL rounds."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class UsageBenchmarkTests(unittest.TestCase):
    def test_complete_mock_conversations(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "scripts/benchmark_usage.py", "--mock", "--output", directory],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((Path(directory) / "benchmark.json").read_text())
            events = [
                json.loads(line)
                for line in (Path(directory) / "usage.jsonl").read_text().splitlines()
            ]
        self.assertEqual(manifest["mode"], "mock")
        self.assertEqual(manifest["status"], "completed")
        self.assertTrue(all(m["error_stages"] == 0 for m in manifest["messages"]))
        self.assertEqual([m["calls"] for m in manifest["messages"]], [1, 1, 1, 3])
        self.assertEqual(len({m["request_id"] for m in manifest["messages"]}), 4)
        self.assertTrue(all(m["error_calls"] == 0 for m in manifest["messages"]))
        for message in manifest["messages"]:
            selected = [e for e in events if e["request_id"] == message["request_id"]]
            self.assertTrue(any(e["stage"] == "delivery" for e in selected))
            self.assertEqual([e["sequence"] for e in selected], list(range(1, len(selected) + 1)))
        sql_events = [
            e for e in events if e["request_id"] == manifest["messages"][-1]["request_id"]
        ]
        calls = [e for e in sql_events if e["event"] == "usage"]
        self.assertEqual([c["round"] for c in calls], [1, 2, 3])
        self.assertEqual(
            [c["after_tool"] for c in calls], [None, "describir_tablas", "consultar_sql"]
        )
        self.assertTrue(all(c["system_chars_by_section"]["business_context"] > 0 for c in calls))
        self.assertGreater(calls[-1]["message_chars_by_role"]["tool"], 0)

    def test_synthetic_expenses_exclude_cancelled_rows(self):
        from scripts.benchmark_usage import DemoDatabase, EXPENSES

        db = DemoDatabase()
        self.addCleanup(db.conn.close)
        result = db.query(f"SELECT SUM(IMPORTE) AS total FROM {EXPENSES} WHERE ESTADO = 'ACTIVA'")
        self.assertEqual(result["rows"], [["42"]])
        schema = json.loads(db.schema([EXPENSES]))
        self.assertIn("ESTADO", [c["name"] for c in schema[0]["columns"]])

    def test_sql_failure_does_not_report_completed(self):
        from scripts.benchmark_usage import DemoDatabase, EXPENSES, main

        original = DemoDatabase.query

        def fail_expenses(db, sql, *args, **kwargs):
            if EXPENSES in sql:
                raise RuntimeError("Synthetic SQL failure")
            return original(db, sql, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict("os.environ"),
                patch.object(DemoDatabase, "query", fail_expenses),
                patch.object(sys, "argv", ["benchmark_usage.py", "--mock", "--output", directory]),
            ):
                self.assertEqual(main(), 1)
            manifest = json.loads((Path(directory) / "benchmark.json").read_text())
        self.assertEqual(manifest["status"], "completed_with_errors")
        self.assertGreater(manifest["messages"][-1]["error_stages"], 0)
        self.assertEqual(manifest["messages"][-1]["error_calls"], 0)


class UsageComparisonTests(unittest.TestCase):
    def test_checkout_sources_override_an_old_installed_package_in_parent_and_children(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stale = folder / "old-install" / "dbagg"
            stale.mkdir(parents=True)
            (stale / "__init__.py").write_text(
                "raise RuntimeError('Old installed dbagg was selected')\n", encoding="utf-8"
            )
            output = folder / "results"
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "compare_usage.py"),
                    "--mock",
                    "--output",
                    str(output),
                ],
                # Also exercise invocation outside the checkout's working directory.
                cwd=folder,
                env=dict(os.environ, PYTHONPATH=str(stale.parent)),
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report["variants"]), 3)
            self.assertTrue(all(v["correct_answers"] == 4 for v in report["variants"]))

    def test_all_variants_measure_complete_answers_and_jev(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "scripts/compare_usage.py", "--mock", "--output", directory],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(directory) / "comparison.json").read_text(encoding="utf-8"))
            self.assertTrue((Path(directory) / "comparison.html").is_file())
            self.assertEqual(
                [v["variant"] for v in report["variants"]], ["legacy", "reference", "jev"]
            )
            self.assertTrue(
                all(
                    v["correct_answers"] == 4 and v["status"] == "completed"
                    for v in report["variants"]
                )
            )
            jev = report["variants"][2]
            self.assertEqual(jev["totals"]["providers"], {"jev": 3, "openai": 4})
            self.assertEqual(jev["totals"]["known_total_tokens"], 4506)
            self.assertEqual(jev["totals"]["unknown_total_tokens_calls"], 0)
            events = [
                json.loads(line)
                for line in (Path(directory) / "jev-1" / "usage.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(
                any(e.get("provider") == "jev" and e.get("task") == "routing" for e in events)
            )
            self.assertEqual(len({e["request_id"] for e in events}), 4)
            self.assertNotIn(
                "synthetic-sender",
                (Path(directory) / "jev-1" / "usage.jsonl").read_text(encoding="utf-8"),
            )

    def test_missing_jev_key_blocks_comparison_before_any_api_calls(self):
        from scripts.compare_usage import main

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic"}, clear=True),
                patch("scripts.compare_usage.load_dotenv"),
                patch("scripts.compare_usage.subprocess.run") as run,
            ):
                self.assertEqual(main(["--output", directory]), 2)
                run.assert_not_called()

    def test_unknown_usage_and_cost_are_not_silently_zero(self):
        from scripts.compare_usage import totals

        result = totals(
            [
                {
                    "event": "usage",
                    "provider": "jev",
                    "model": "jev-1.14.0",
                    "status": "error",
                    "duration_ms": 10,
                    "total_tokens": None,
                }
            ]
        )
        self.assertEqual(result["unknown_total_tokens_calls"], 1)
        self.assertEqual(result["unknown_cost_calls"], 1)
