"""Four complete conversations verify metering across history and SQL rounds."""

import json
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
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((Path(directory) / "benchmark.json").read_text())
            events = [json.loads(line) for line in (Path(directory) / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(manifest["mode"], "mock")
        self.assertEqual(manifest["status"], "completed")
        self.assertTrue(all(m["error_stages"] == 0 for m in manifest["messages"]))
        self.assertEqual([m["calls"] for m in manifest["messages"]], [1, 1, 1, 3])
        self.assertEqual(len({m["request_id"] for m in manifest["messages"]}), 4)
        self.assertTrue(all(m["error_calls"] == 0 for m in manifest["messages"]))
        for message in manifest["messages"]:
            selected = [e for e in events if e["request_id"] == message["request_id"]]
            self.assertTrue(any(e["stage"] == "delivery" for e in selected))
            self.assertEqual([e["sequence"] for e in selected], list(range(1, len(selected)+1)))
        sql_events = [e for e in events if e["request_id"] == manifest["messages"][-1]["request_id"]]
        calls = [e for e in sql_events if e["event"] == "usage"]
        self.assertEqual([c["round"] for c in calls], [1, 2, 3])
        self.assertEqual([c["after_tool"] for c in calls], [None, "describir_tablas", "consultar_sql"])
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
            with patch.dict("os.environ"), patch.object(DemoDatabase, "query", fail_expenses), patch.object(
                sys, "argv", ["benchmark_usage.py", "--mock", "--output", directory]
            ):
                self.assertEqual(main(), 1)
            manifest = json.loads((Path(directory) / "benchmark.json").read_text())
        self.assertEqual(manifest["status"], "completed_with_errors")
        self.assertGreater(manifest["messages"][-1]["error_stages"], 0)
        self.assertEqual(manifest["messages"][-1]["error_calls"], 0)
