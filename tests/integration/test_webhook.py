import hashlib
import hmac
import json
import unittest
import httpx
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from dbagg.config import Settings
from dbagg.api.app import create_app


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            "dummy",
            "dummy",
            "dummy",
            "test-secret",
            "verify",
            "123",
            "v23.0",
            frozenset({"5215555555555"}),
            frozenset({"proadel.vw_alertascobranza"}),
            "unused",
        )
        self.assistant = Mock()
        self.assistant.answer.return_value = "Respuesta de prueba"
        self.send = Mock()
        self.client = TestClient(create_app(self.settings, self.assistant, self.send))

    def payload(self, sender="5215555555555", phone="123"):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": phone},
                                "messages": [
                                    {
                                        "id": "msg1",
                                        "from": sender,
                                        "type": "text",
                                        "text": {"body": "saldo del cliente A"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }

    def post(self, payload, signed=True):
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        return self.client.post(
            "/webhook", content=body, headers={"x-hub-signature-256": sig if signed else "bad"}
        )

    def test_meta_verification(self):
        response = self.client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "verify", "hub.challenge": "42"},
        )
        self.assertEqual(response.text, "42")
        self.assertEqual(self.client.get("/webhook").status_code, 403)

    def test_unsigned_or_unauthorized_messages_never_reach_llm(self):
        self.assertEqual(self.post(self.payload(), signed=False).status_code, 403)
        self.assertEqual(self.post(self.payload(sender="9999999999")).status_code, 200)
        self.assertEqual(self.post(self.payload(phone="999")).status_code, 200)
        self.assistant.answer.assert_not_called()
        self.send.assert_not_called()

    def test_authorized_message_processed_once(self):
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assistant.answer.assert_called_once_with("saldo del cliente A")
        self.send.assert_called_once_with("5215555555555", "Respuesta de prueba")

    def test_errors_do_not_expose_credentials(self):
        self.assistant.answer.side_effect = RuntimeError("PWD=secret")
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            self.post(self.payload())
        self.assertNotIn("PWD=secret", "\n".join(logs.output))
        self.assertNotIn("secret", self.send.call_args.args[1])

    def test_send_failure_logs_only_http_status_and_numeric_meta_codes(self):
        request = httpx.Request(
            "POST",
            "https://graph.facebook.com/v25.0/123/messages",
            headers={"Authorization": "Bearer secret-key"},
        )
        response = httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": 190,
                    "error_subcode": 463,
                    "message": "private token secret-key",
                    "error_data": {"details": "private recipient"},
                    "fbtrace_id": "private-id",
                }
            },
        )
        self.send.side_effect = httpx.HTTPStatusError(
            "secret-key", request=request, response=response
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            self.post(self.payload())
        output = "\n".join(logs.output)
        self.assertIn("http_status=400 meta_code=190 meta_subcode=463", output)
        self.assertNotIn("secret-key", output)
        self.assertNotIn("private", output)
        self.assertNotIn("graph.facebook.com", output)

    def test_diagnostic_identifies_local_agent_without_llm(self):
        payload = self.payload()
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "/diagnostico"
        self.post(payload)
        self.assistant.answer.assert_not_called()
        self.assertIn("dbagg:", self.send.call_args.args[1])

    def test_followup_receives_previous_question_and_answer(self):
        self.assistant.answer.return_value = "¿Quieres consultar las ventas de esta semana?"
        with patch("dbagg.services.memory.time.monotonic", return_value=100):
            self.post(self.payload())
        payload = self.payload()
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message.update(id="msg2", text={"body": "sí"})
        with patch("dbagg.services.memory.time.monotonic", return_value=111):
            self.post(payload)
        self.assertEqual(self.assistant.answer.call_args.args, ("sí",))
        history = self.assistant.answer.call_args.kwargs["history"]
        self.assertEqual(history[-1]["content"], "¿Quieres consultar las ventas de esta semana?")
        self.assertEqual(history[0]["content"], "saldo del cliente A")
