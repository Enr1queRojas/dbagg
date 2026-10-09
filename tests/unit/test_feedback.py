import json
import tempfile
import unittest
from pathlib import Path

from dbagg.evaluation.store import FeedbackStore, validate_training_case


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = FeedbackStore(self.root / "feedback.sqlite3")
        self.interaction = {
            "id": "case-1",
            "created_at": "2026-10-09T00:00:00+00:00",
            "question": "Pregunta privada",
            "answer": "Respuesta privada",
            "history": [],
            "model": "test-model",
            "prompt_version": "v1",
            "context_version": "abc",
            "sender": "this-metadata-must-not-be-stored",
        }
        self.prepared = {
            "anonymized": True,
            "verified": True,
            "messages": [
                {"role": "user", "content": "¿Cuánto debe CLIENTE_DEMO?"},
                {"role": "assistant", "content": "El saldo actual de CLIENTE_DEMO es 100."},
            ],
        }

    def test_export_requires_review_and_uses_sanitized_case_not_original(self):
        self.store.record(self.interaction, "bad", "El saldo estaba mal.")
        empty = self.root / "empty.jsonl"
        self.assertEqual(self.store.export(empty), 0)
        self.store.review("case-1", "approved", "revisor", self.prepared)
        output = self.root / "approved.jsonl"
        self.assertEqual(self.store.export(output), 1)
        content = output.read_text(encoding="utf-8")
        self.assertNotIn("privada", content)
        self.assertNotIn("must-not-be-stored", str(self.store.get("case-1")))
        self.assertEqual(json.loads(content), {"messages": self.prepared["messages"]})
        with self.assertRaises(FileExistsError):
            self.store.export(output)

    def test_changing_rating_invalidates_previous_review(self):
        self.store.record(self.interaction, "good")
        self.store.review("case-1", "approved", "revisor", self.prepared)
        self.store.record(self.interaction, "bad", "Revisar fecha")
        record = self.store.get("case-1")
        self.assertEqual(record["review_status"], "pending")
        self.assertIsNone(record["prepared_case_json"])
        self.assertEqual(
            self.store.summary(), [{"rating": "bad", "review_status": "pending", "count": 1}]
        )

    def test_unreviewed_or_invalid_examples_cannot_be_approved(self):
        self.store.record(self.interaction, "good")
        for case in (
            None,
            {},
            dict(self.prepared, anonymized=False),
            dict(self.prepared, verified=False),
            dict(self.prepared, messages=[{"role": "system", "content": "x"}]),
        ):
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.store.review("case-1", "approved", "revisor", case)
        self.assertEqual(self.store.get("case-1")["review_status"], "pending")
        with self.assertRaises(ValueError):
            validate_training_case(dict(self.prepared, messages=[]))

    def test_rejection_is_not_exported(self):
        self.store.record(self.interaction, "good")
        self.store.review("case-1", "rejected", "revisor")
        self.assertEqual(self.store.export(self.root / "rejected.jsonl"), 0)


if __name__ == "__main__":
    unittest.main()
