import unittest
from datetime import date
from unittest.mock import patch
from dbagg.services.memory import ConversationMemory
from dbagg.agent.prompts import reporting_calendar


class MemoryTests(unittest.TestCase):
    def test_sender_isolation_and_four_turn_limit(self):
        memory = ConversationMemory()
        for i in range(6):
            memory.remember("one", str(i), "answer")
        self.assertEqual(len(memory.history("one")), 8)
        self.assertEqual(memory.history("two"), [])
        copy = memory.history("one")
        copy[0]["content"] = "changed"
        self.assertNotEqual(memory.history("one")[0]["content"], "changed")
        memory.clear("one")
        self.assertEqual(memory.history("one"), [])

    def test_memory_expires(self):
        memory = ConversationMemory()
        with patch("dbagg.services.memory.time.monotonic", return_value=0):
            memory.remember("one", "question", "answer")
        with patch("dbagg.services.memory.time.monotonic", return_value=1801):
            self.assertEqual(memory.history("one"), [])

    def test_week_boundary_including_all_of_today(self):
        self.assertEqual(
            reporting_calendar(date(2026, 10, 7)),
            {"today": "2026-10-07", "week_start": "2026-10-05", "week_end_exclusive": "2026-10-08"},
        )
        self.assertEqual(reporting_calendar(date(2026, 10, 5))["week_start"], "2026-10-05")
