import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from dbagg.api.app import create_app
from dbagg.evaluation.store import FeedbackStore
from tests.integration import test_webhook


class FeedbackWebhookTests(unittest.TestCase):
    def setUp(self):
        # Reuse payload/signature helpers without inheriting and rerunning the entire suite.
        self.webhook = test_webhook.WebhookTests()
        self.webhook.setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = FeedbackStore(Path(self.folder.name) / "feedback.sqlite3")
        self.webhook.client = TestClient(
            create_app(self.webhook.settings, self.webhook.assistant, self.webhook.send, self.store)
        )

    def message(self, text, identifier):
        payload = self.webhook.payload()
        payload["entry"][0]["changes"][0]["value"]["messages"][0].update(
            id=identifier, text={"body": text}
        )
        return payload

    def test_immediate_rating_records_the_last_delivered_answer_without_llm_call(self):
        with patch("dbagg.services.memory.time.monotonic", return_value=100):
            self.webhook.post(self.message("¿Cuánto debe CLIENTE_DEMO?", "query"))
            self.webhook.post(self.message("/mala El saldo está mal", "rating"))
        self.webhook.assistant.answer.assert_called_once()
        pending = self.store.list_pending()
        self.assertEqual(len(pending), 1)
        record = self.store.get(pending[0]["id"])
        self.assertEqual(record["question"], "¿Cuánto debe CLIENTE_DEMO?")
        self.assertEqual(record["answer"], "Respuesta de prueba")
        self.assertEqual(record["rating"], "bad")
        self.assertNotIn("5215555555555", str(record))

    def test_reset_clears_which_answer_can_be_rated(self):
        with patch("dbagg.services.memory.time.monotonic", return_value=100):
            self.webhook.post(self.message("Pregunta", "query"))
            self.webhook.post(self.message("/reiniciar", "reset"))
            self.webhook.post(self.message("/buena", "rating"))
        self.assertEqual(self.store.list_pending(), [])
        self.assertIn("No hay una respuesta reciente", self.webhook.send.call_args.args[1])

    def test_feedback_is_disabled_by_default(self):
        self.webhook.client = TestClient(
            create_app(self.webhook.settings, self.webhook.assistant, self.webhook.send)
        )
        self.webhook.post(self.message("/buena", "rating"))
        self.webhook.assistant.answer.assert_not_called()
        self.assertIn("no están habilitadas", self.webhook.send.call_args.args[1])

    def test_another_authorized_sender_cannot_rate_someone_elses_answer(self):
        other = "5215555555556"
        settings = replace(self.webhook.settings, numbers=self.webhook.settings.numbers | {other})
        self.webhook.client = TestClient(
            create_app(settings, self.webhook.assistant, self.webhook.send, self.store)
        )
        self.webhook.post(self.message("Pregunta del primer usuario", "query"))
        payload = self.message("/buena", "rating")
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = other
        self.webhook.post(payload)
        self.assertEqual(self.store.list_pending(), [])
        self.assertIn("No hay una respuesta reciente", self.webhook.send.call_args.args[1])

    def test_undelivered_answer_is_not_available_for_rating(self):
        self.webhook.send.side_effect = RuntimeError("Delivery failed")
        with self.assertLogs("uvicorn.error", level="WARNING"):
            self.webhook.post(self.message("Pregunta", "query"))
        self.webhook.send.side_effect = None
        self.webhook.post(self.message("/buena", "rating"))
        self.assertEqual(self.store.list_pending(), [])
        self.assertIn("No hay una respuesta reciente", self.webhook.send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
