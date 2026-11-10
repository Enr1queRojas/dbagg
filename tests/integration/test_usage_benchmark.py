"""Four complete conversations verify metering across history and SQL rounds."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
