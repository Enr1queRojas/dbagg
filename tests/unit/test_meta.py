import json
import unittest
import httpx
from dbagg.integrations.meta import meta_error_codes


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
