"""Signed webhook through the real agent and business layer, with synthetic dependencies."""

import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from dbagg.agent.service import Assistant
from dbagg.api.app import create_app
from dbagg.config import Settings
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase


class BusinessWebhookTests(unittest.TestCase):
    def test_balance_then_followup_keeps_customer_and_delivers_sql_values(self):
        # Requests are sequential; TestClient dispatches them on a worker thread.
        db = SyntheticDatabase(check_same_thread=False)
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Prueba", 100)
        db.payment(1, "2023-01-25", 20, customer="DEMO1")
        model = Mock()

        def completion(operation, customer):
            call = SimpleNamespace(
                id="call",
                function=SimpleNamespace(
                    name="consultar_negocio",
                    arguments=json.dumps({"operation": operation, "customer": customer}),
                ),
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Importe inventado: 999", tool_calls=[call])
                    )
                ]
            )

        model.chat.completions.create.side_effect = [
            completion("saldo", "Prueba"),
            completion("ultimo_pago", "DEMO1"),
        ]
        settings = Settings(
            "dummy",
            "dummy",
            "dummy",
            "test-secret",
            "verify",
            "123",
            "v25.0",
            frozenset({"5215555555555"}),
            frozenset({CUSTOMERS, PAYMENTS}),
            "unused",
        )
        assistant = Assistant(settings, db=db, client=model)
        deliver = Mock()
        with TestClient(create_app(settings, assistant, deliver)) as client:
            for i, question in enumerate(("¿Cuánto debe Prueba?", "¿Y su último pago?")):
                payload = {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "changes": [
                                {
                                    "value": {
                                        "metadata": {"phone_number_id": "123"},
                                        "messages": [
                                            {
                                                "id": f"msg{i}",
                                                "from": "5215555555555",
                                                "type": "text",
                                                "text": {"body": question},
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    ],
                }
                body = json.dumps(payload).encode()
                signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
                with patch("dbagg.services.memory.time.monotonic", return_value=100 + i * 11):
                    response = client.post(
                        "/webhook", content=body, headers={"x-hub-signature-256": signature}
                    )
                self.assertEqual(response.status_code, 200)
        self.assertEqual(deliver.call_count, 2)
        balance, payment = [call.args[1] for call in deliver.call_args_list]
        self.assertIn("100.00", balance)
        self.assertIn("2023-01-25", payment)
        self.assertIn("20.00", payment)
        self.assertNotIn("999", balance + payment)
        messages = model.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[2]["content"], balance)
