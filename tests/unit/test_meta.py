import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from dbagg.integrations.meta import MetaClient, meta_error_codes


class SendDocumentTests(unittest.TestCase):
    PDF = b"%PDF-contenido-privado"

    def setUp(self):
        self.requests = []
        self.uploads = []
        self.messages = []
        sleep = patch("dbagg.integrations.meta.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def client(self):
        def handler(request):
            self.requests.append(request)
            path = request.url.path
            queue = self.uploads if path.endswith("/media") else self.messages
            if queue:
                reply = queue.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
            body = {"id": "M1"} if path.endswith("/media") else {"messages": [{"id": "w1"}]}
            return httpx.Response(200, json=body)

        settings = SimpleNamespace(phone_id="123", graph_version="v25.0", meta_token="SECRETO")
        return MetaClient(settings, transport=httpx.MockTransport(handler))

    def test_document_is_uploaded_then_sent_with_its_filename(self):
        self.client().send_document("521", self.PDF, "estado.pdf")
        upload, message = self.requests
        self.assertTrue(upload.url.path.endswith("/123/media"))
        self.assertEqual(upload.headers["authorization"], "Bearer SECRETO")
        self.assertIn(b"application/pdf", upload.content)
        self.assertIn(b"whatsapp", upload.content)
        self.assertIn(self.PDF, upload.content)
        self.assertTrue(message.url.path.endswith("/123/messages"))
        payload = json.loads(message.content)
        self.assertEqual(payload["to"], "521")
        self.assertEqual(payload["type"], "document")
        self.assertEqual(payload["document"], {"id": "M1", "filename": "estado.pdf"})

    def test_upload_retries_after_a_server_error(self):
        self.uploads.append(httpx.Response(500))
        self.client().send_document("521", self.PDF, "estado.pdf")
        self.assertEqual(len(self.requests), 3)

    def test_timeouts_are_retried(self):
        self.uploads.append(httpx.ReadTimeout("lento"))
        self.client().send_document("521", self.PDF, "estado.pdf")
        self.assertEqual(len(self.requests), 3)

    def test_message_retry_does_not_upload_twice(self):
        self.messages.append(httpx.Response(429))
        self.client().send_document("521", self.PDF, "estado.pdf")
        paths = [request.url.path.rsplit("/", 1)[-1] for request in self.requests]
        self.assertEqual(paths, ["media", "messages", "messages"])

    def test_exhausted_retries_raise(self):
        self.uploads.extend(httpx.Response(503) for _ in range(3))
        with self.assertRaises(httpx.HTTPStatusError):
            self.client().send_document("521", self.PDF, "estado.pdf")
        self.assertEqual(len(self.requests), 3)

    def test_client_error_is_not_retried(self):
        self.uploads.append(httpx.Response(400, json={"error": {"code": 100}}))
        with self.assertRaises(httpx.HTTPStatusError):
            self.client().send_document("521", self.PDF, "estado.pdf")
        self.assertEqual(len(self.requests), 1)

    def test_failed_message_after_upload_raises(self):
        self.messages.append(httpx.Response(400, json={"error": {"code": 131030}}))
        with self.assertRaises(httpx.HTTPStatusError):
            self.client().send_document("521", self.PDF, "estado.pdf")

    def test_upload_without_id_fails(self):
        self.uploads.append(httpx.Response(200, json={}))
        with self.assertRaises(ValueError):
            self.client().send_document("521", self.PDF, "estado.pdf")
        self.assertEqual(len(self.requests), 1)

    def test_document_bytes_and_token_are_not_logged(self):
        self.uploads.append(httpx.Response(500))
        with self.assertLogs("uvicorn.error", level="INFO") as logs:
            self.client().send_document("521", self.PDF, "estado-privado.pdf")
        output = "\n".join(logs.output)
        for secret in ("contenido-privado", "SECRETO", "estado-privado", "521"):
            self.assertNotIn(secret, output)


class MetaDiagnosticsTests(unittest.TestCase):
    def test_invalid_error_payloads_never_expose_arbitrary_text(self):
        for payload in (
            [],
            None,
            "secret",
            {"error": "secret"},
            {"error": None},
            {"error": {"code": "secret", "error_subcode": {"secret": "value"}}},
            {"error": {"code": True, "error_subcode": -1}},
        ):
            with self.subTest(payload_type=type(payload).__name__):
                response = httpx.Response(400, content=json.dumps(payload))
                self.assertEqual(meta_error_codes(response), (None, None))
        self.assertEqual(
            meta_error_codes(httpx.Response(502, text="<html>secret</html>")), (None, None)
        )
