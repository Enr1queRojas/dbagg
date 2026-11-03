import hashlib
import hmac
import json
import logging
import time
import unittest
import httpx
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from dbagg.config import Settings
from dbagg.api.app import RedactVerifyToken, create_app
from dbagg.services.conversation import UNAUTHORIZED_NOTICE, ConversationService
from dbagg.services.memory import UnauthorizedNotices


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

    def test_application_closes_only_the_assistant_it_owns(self):
        with patch("dbagg.api.app.Assistant") as factory:
            with TestClient(create_app(self.settings)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
            factory.return_value.close.assert_called_once()
        with TestClient(create_app(self.settings, self.assistant, self.send)):
            pass
        self.assistant.close.assert_not_called()

    def test_unsigned_or_unauthorized_messages_never_reach_llm(self):
        self.assertEqual(self.post(self.payload(), signed=False).status_code, 403)
        self.assertEqual(
            self.post(self.payload(sender="9999999999"), signed=False).status_code, 403
        )
        self.assertEqual(self.post(self.payload(phone="999")).status_code, 200)
        self.assertEqual(self.post(self.payload(sender="9999999999", phone="999")).status_code, 200)
        self.assistant.answer.assert_not_called()
        self.send.assert_not_called()

    def stranger(self, kind="text", **message):
        payload = self.payload(sender="9999999999")
        original = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        original.update(type=kind, **message)
        return payload

    def test_a_stranger_only_receives_the_fixed_support_notice(self):
        self.assertEqual(self.post(self.stranger()).status_code, 200)
        self.send.assert_called_once_with("9999999999", UNAUTHORIZED_NOTICE)
        self.assistant.answer.assert_not_called()
        self.assertIn("soporte", UNAUTHORIZED_NOTICE)

    def test_any_message_type_from_a_stranger_gets_the_notice_without_processing(self):
        voice = Mock()
        self.client = TestClient(create_app(self.settings, self.assistant, self.send, voice=voice))
        self.assertEqual(self.post(self.stranger("audio", audio={"id": "123"})).status_code, 200)
        voice.text.assert_not_called()
        self.assistant.answer.assert_not_called()
        self.send.assert_called_once_with("9999999999", UNAUTHORIZED_NOTICE)

    def test_a_stranger_cannot_use_control_commands(self):
        payload = self.stranger(text={"body": "/diagnostico"})
        self.assertEqual(self.post(payload).status_code, 200)
        self.send.assert_called_once_with("9999999999", UNAUTHORIZED_NOTICE)

    def test_a_stranger_is_answered_only_once_a_day(self):
        for number in range(3):
            self.post(self.stranger(id=f"m{number}"))
        self.send.assert_called_once_with("9999999999", UNAUTHORIZED_NOTICE)

    def test_an_old_or_unsigned_message_from_a_stranger_gets_no_reply(self):
        self.post(self.stranger(timestamp=str(int(time.time()) - 3600)))
        self.post(self.stranger(id="other"), signed=False)
        self.send.assert_not_called()

    def test_notices_have_a_global_hourly_cap(self):
        notices = UnauthorizedNotices()
        claims = [notices.claim(str(n)) for n in range(UnauthorizedNotices.MAX_PER_HOUR + 5)]
        self.assertEqual(sum(claims), UnauthorizedNotices.MAX_PER_HOUR)

    def test_the_notice_never_touches_stored_data(self):
        deliver, outbox = Mock(), Mock()
        service = ConversationService(self.settings, self.assistant, deliver, outbox=outbox)
        service.notify_unauthorized("9999999999")
        deliver.assert_called_once_with("9999999999", UNAUTHORIZED_NOTICE)
        self.assertEqual(outbox.mock_calls, [])
        self.assertEqual(self.assistant.mock_calls, [])

    def test_a_notice_rejected_by_meta_logs_its_code_but_not_the_number(self):
        request = httpx.Request("POST", "https://graph.facebook.com")
        response = httpx.Response(400, json={"error": {"code": 131030}}, request=request)
        self.send.side_effect = httpx.HTTPStatusError("x", request=request, response=response)
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            self.post(self.stranger())
        self.assertIn("meta_code=131030", logs.output[0])
        self.assertNotIn("9999999999", logs.output[0])

    def test_a_transient_failure_does_not_spend_the_notice(self):
        self.send.side_effect = [httpx.ConnectError("sin red"), None]
        self.post(self.stranger(id="m1"))
        self.post(self.stranger(id="m2"))
        self.assertEqual(self.send.call_count, 2)

    def test_a_definitive_rejection_is_not_retried_on_every_message(self):
        request = httpx.Request("POST", "https://graph.facebook.com")
        response = httpx.Response(400, json={"error": {"code": 131030}}, request=request)
        self.send.side_effect = httpx.HTTPStatusError("x", request=request, response=response)
        self.post(self.stranger(id="m1"))
        self.post(self.stranger(id="m2"))
        self.assertEqual(self.send.call_count, 1)

    def test_released_notices_still_count_for_the_hourly_cap(self):
        notices = UnauthorizedNotices()
        for _ in range(UnauthorizedNotices.MAX_PER_HOUR):
            self.assertTrue(notices.claim("1"))
            notices.release("1")
        self.assertFalse(notices.claim("1"))

    def test_a_failed_notice_does_not_break_the_service(self):
        self.send.side_effect = RuntimeError("token=secret")
        self.assertEqual(self.post(self.stranger()).status_code, 200)

    def test_an_authorized_number_is_unaffected_by_strangers(self):
        self.post(self.stranger())
        self.send.reset_mock()
        self.post(self.payload())
        self.assistant.answer.assert_called_once_with("saldo del cliente A")
        self.send.assert_called_once_with("5215555555555", "Respuesta de prueba")

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

    def test_model_http_error_logs_status_without_provider_response_or_key(self):
        request = httpx.Request(
            "POST", "https://example.test/messages", headers={"x-api-key": "private-key"}
        )
        response = httpx.Response(401, request=request, json={"error": "private response"})
        self.assistant.answer.side_effect = httpx.HTTPStatusError(
            "private-key", request=request, response=response
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            self.post(self.payload())
        output = "\n".join(logs.output)
        self.assertIn("stage=answer_failed", output)
        self.assertIn("http_status=401", output)
        self.assertNotIn("private", output)
        self.assertNotIn("example.test", output)

    def test_replayed_old_or_malformed_timestamp_is_not_answered(self):
        payload = self.payload()
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        for number, timestamp in enumerate((str(int(time.time()) - 3600), "ayer"), start=2):
            message.update(id=f"msg{number}", timestamp=timestamp)
            self.assertEqual(self.post(payload).status_code, 200)
        self.assistant.answer.assert_not_called()
        message.update(id="msg9", timestamp=str(int(time.time()) - 60))
        self.post(payload)
        self.assistant.answer.assert_called_once()

    def test_access_log_never_shows_the_verify_token(self):
        path = "/webhook?hub.mode=subscribe&hub.verify_token=verify&x=1"
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4", "GET", path, "1.1", 200),
            None,
        )
        RedactVerifyToken().filter(record)
        self.assertIn("hub.verify_token=<redactado>&x=1", record.getMessage())
        access = logging.getLogger("uvicorn.access")
        self.assertTrue(any(isinstance(f, RedactVerifyToken) for f in access.filters))

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
