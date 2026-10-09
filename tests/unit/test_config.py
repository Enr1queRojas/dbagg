import os
import unittest
from unittest.mock import patch
from dbagg.config import Settings


class TLSSettingsTests(unittest.TestCase):
    def settings(self, encrypt="yes", trust="yes", opt_in="false"):
        values = dict(
            OPENAI_API_KEY="test",
            META_ACCESS_TOKEN="test",
            META_APP_SECRET="test",
            META_VERIFY_TOKEN="test",
            META_PHONE_NUMBER_ID="123",
            META_GRAPH_VERSION="v25.0",
            WHATSAPP_ALLOWED_NUMBERS="5215555555555",
            SQL_ALLOWED_TABLES="proadel.demo",
            DB_CONNECTION_STRING=f"DRIVER={{test}};Encrypt={encrypt};TrustServerCertificate={trust};",
            DB_ALLOW_UNVERIFIED_TLS=opt_in,
        )
        with patch.dict(os.environ, values, clear=True), patch("dbagg.config.load_dotenv"):
            return Settings.from_env()

    def test_default_rejects_unverified_tls(self):
        with self.assertRaises(ValueError):
            self.settings()

    def test_demo_requires_explicit_opt_in_and_warns(self):
        with self.assertLogs("uvicorn.error", level="WARNING"):
            self.settings(opt_in="true")

    def test_demo_still_rejects_disabled_encryption(self):
        with self.assertRaises(ValueError):
            self.settings(encrypt="no", opt_in="true")

    def test_verified_tls_needs_no_exception(self):
        self.settings(trust="no")
