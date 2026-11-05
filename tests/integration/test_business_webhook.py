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
from tests.unit.test_supplier_queries import PURCHASES, SUPPLIER_PAYMENTS, SUPPLIERS
from tests.unit.test_statements import DEFINITIONS as STATEMENT_DEFINITIONS
from tests.unit.test_statements import PURCHASES as STATEMENT_PURCHASES
from tests.unit.test_statements import StatementDatabase
from tests.unit.test_supplier_routing import RoutingDatabase


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
                        finish_reason="tool_calls",
                        message=SimpleNamespace(
                            content="Importe inventado: 999", tool_calls=[call]
                        ),
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

    def test_supplier_context_survives_two_webhook_turns(self):
        db = RoutingDatabase(check_same_thread=False)
        self.addCleanup(db.conn.close)
        db.purchase(1, 7, "C1", "2026-01-25", 300)
        model = Mock()

        def completion(arguments):
            call = SimpleNamespace(
                id="call",
                function=SimpleNamespace(name="consultar_negocio", arguments=json.dumps(arguments)),
            )
            message = SimpleNamespace(content="Importe inventado: 999", tool_calls=[call])
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="tool_calls", message=message)]
            )

        model.chat.completions.create.side_effect = [
            completion({"operation": "saldo", "customer": "Cuervo"}),
            completion({"operation": "ultima_nota", "customer": "C1", "codigo_exacto": True}),
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
            frozenset({CUSTOMERS, PAYMENTS, SUPPLIERS, SUPPLIER_PAYMENTS, PURCHASES}),
            "unused",
        )
        assistant = Assistant(settings, db=db, client=model)
        deliver = Mock()
        questions = ("¿Cuál es el saldo de Cuervo?", "¿Y su última nota?")
        with TestClient(create_app(settings, assistant, deliver)) as client:
            for i, question in enumerate(questions):
                message = {
                    "id": f"sup{i}",
                    "from": "5215555555555",
                    "type": "text",
                    "text": {"body": question},
                }
                value = {"metadata": {"phone_number_id": "123"}, "messages": [message]}
                body = json.dumps(
                    {
                        "object": "whatsapp_business_account",
                        "entry": [{"changes": [{"value": value}]}],
                    }
                ).encode()
                signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
                with patch("dbagg.services.memory.time.monotonic", return_value=100 + i * 11):
                    response = client.post(
                        "/webhook", content=body, headers={"x-hub-signature-256": signature}
                    )
                self.assertEqual(response.status_code, 200)
        balance, note = [call.args[1] for call in deliver.call_args_list]
        self.assertTrue(balance.startswith("Proveedor Cuervo"))
        self.assertIn("Le debemos: 44.00", balance)
        self.assertTrue(note.startswith("Proveedor Cuervo"))
        self.assertIn("folio 7", note)
        self.assertNotIn("999", balance + note)

    def test_statement_flow_over_the_webhook(self):
        db = StatementDatabase(check_same_thread=False)
        self.addCleanup(db.conn.close)
        db.supplier("C1", "300", name="Cuervo")
        db.line(STATEMENT_PURCHASES, "C1", 1, "2026-01-25", "300", folio=7)
        model = Mock()

        def completion(arguments):
            call = SimpleNamespace(
                id="call",
                function=SimpleNamespace(name="consultar_negocio", arguments=json.dumps(arguments)),
            )
            message = SimpleNamespace(content="Importe inventado: 999", tool_calls=[call])
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="tool_calls", message=message)]
            )

        model.chat.completions.create.side_effect = [
            completion({"operation": "estado_cuenta", "customer": "Cuervo"}),
            completion(
                {
                    "operation": "estado_cuenta",
                    "customer": "C1",
                    "codigo_exacto": True,
                    "period": "rango",
                    "start": "2026-01-01",
                    "end": "2026-01-31",
                }
            ),
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
            frozenset(STATEMENT_DEFINITIONS),
            "unused",
        )
        assistant = Assistant(settings, db=db, client=model)
        deliver = Mock()
        send_document = Mock()
        questions = ("Estado de cuenta de Cuervo", "de enero de 2026")
        with TestClient(
            create_app(settings, assistant, deliver, send_document=send_document)
        ) as client:
            for i, question in enumerate(questions):
                message = {
                    "id": f"st{i}",
                    "from": "5215555555555",
                    "type": "text",
                    "text": {"body": question},
                }
                value = {"metadata": {"phone_number_id": "123"}, "messages": [message]}
                body = json.dumps(
                    {
                        "object": "whatsapp_business_account",
                        "entry": [{"changes": [{"value": value}]}],
                    }
                ).encode()
                signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
                with patch("dbagg.services.memory.time.monotonic", return_value=100 + i * 11):
                    response = client.post(
                        "/webhook", content=body, headers={"x-hub-signature-256": signature}
                    )
                self.assertEqual(response.status_code, 200)
        asked, summary = [call.args[1] for call in deliver.call_args_list]
        self.assertIn("¿De qué fechas", asked)
        self.assertIn("Saldo al 31/01/2026: 300.00.", summary)
        self.assertNotIn("999", asked + summary)
        send_document.assert_called_once()
        sender, content, filename = send_document.call_args.args
        self.assertEqual(sender, "5215555555555")
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertTrue(filename.endswith(".pdf"))
