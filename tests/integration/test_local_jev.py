"""The local pilot must use Jev without modifying existing configuration or sending in checks."""

import contextlib
import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI

from scripts.run_local_jev import main, prepare
from dbagg.agent.service import Assistant
from dbagg.api.app import create_app
from dbagg.llm.config import load_llm_config
from dbagg.llm.providers import OpenAICompatibleProvider
from dbagg.routing.jev import JevRouter
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase
from tests.unit.test_llm_providers import call, openai_response


class LocalJevTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.values = {
            "OPENAI_API_KEY": "private-openai-key",
            "TYPESAFE_API_KEY": "private-jev-key",
            "META_ACCESS_TOKEN": "private-meta-token",
            "META_APP_SECRET": "private-app-secret",
            "META_VERIFY_TOKEN": "private-verify-token",
            "META_PHONE_NUMBER_ID": "123",
            "META_GRAPH_VERSION": "v25.0",
            "WHATSAPP_ALLOWED_NUMBERS": "5215555555555",
            "SQL_ALLOWED_TABLES": f"{CUSTOMERS},{PAYMENTS},proadel.gastos_data",
            "DB_CONNECTION_STRING": "DRIVER={test};Encrypt=yes;TrustServerCertificate=no;",
        }
        self.env_file = self.root / ".env"
        self.write_env()
        patcher = patch.dict(os.environ, {"DBAGG_HOME": str(self.root)}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_env(self):
        self.env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in self.values.items()) + "\n", encoding="utf-8"
        )

    def test_prepare_uses_active_jev_and_mini_without_overwriting_local_files(self):
        custom = self.root / "config" / "llm.toml"
        custom.parent.mkdir()
        custom.write_text("existing custom configuration", encoding="utf-8")
        os.environ["LLM_CONFIG"] = str(custom)
        os.environ["LLM_PROFILE"] = "old-profile"
        original = self.env_file.read_bytes()
        settings, summary = prepare()
        self.assertTrue(settings.llm.modular.enabled)
        self.assertEqual(settings.llm.modular.router, "jev")
        self.assertFalse(settings.llm.modular.shadow)
        self.assertEqual(settings.llm.profile.model, "gpt-4.1-mini")
        self.assertEqual(settings.llm.modular.extraction_profile, "openai")
        extraction = settings.llm.profiles[settings.llm.modular.extraction_profile]
        self.assertEqual(extraction.provider, "openai")
        self.assertEqual(extraction.model, "gpt-4.1-mini")
        self.assertEqual(extraction.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(summary["extraction_model"], "gpt-4.1-mini")
        self.assertTrue(summary["extraction_api_key_present"])
        self.assertIsNone(settings.llm.system_prompt)
        self.assertIsNone(settings.llm.tool_descriptions)
        self.assertEqual(custom.read_text(), "existing custom configuration")
        self.assertEqual(self.env_file.read_bytes(), original)
        self.assertEqual(
            summary["usage_path"], str(self.root / "work" / "whatsapp-jev" / "usage.jsonl")
        )
        self.assertTrue(summary["available_routes"])
        self.assertFalse(Path(summary["usage_path"]).exists())

    def test_check_does_not_start_app_or_print_credentials(self):
        output = io.StringIO()
        with (
            patch("scripts.run_local_jev.create_app") as create,
            patch("scripts.run_local_jev.uvicorn.run") as run,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(main(["--check"]), 0)
        create.assert_not_called()
        run.assert_not_called()
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["routing_model"], "jev-1.14.0")
        self.assertFalse(summary["external_services_checked"])
        self.assertNotIn("private-", output.getvalue())
        self.assertNotIn(self.values["WHATSAPP_ALLOWED_NUMBERS"], output.getvalue())

    def test_missing_jev_key_fails_before_app_creation(self):
        self.values.pop("TYPESAFE_API_KEY")
        self.write_env()
        with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
            prepare()

    def test_launch_binds_loopback_one_worker_and_uses_validated_settings(self):
        with (
            patch("scripts.run_local_jev.create_app", return_value="test-app") as create,
            patch("scripts.run_local_jev.uvicorn.run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["--port", "8100"]), 0)
        self.assertEqual(create.call_args.kwargs["settings"].llm.modular.router, "jev")
        run.assert_called_once_with(
            "test-app", host="127.0.0.1", port=8100, workers=1, access_log=False
        )

    def test_explicit_usage_path_is_preserved(self):
        os.environ["DBAGG_USAGE_PATH"] = str(self.root / "custom-usage.jsonl")
        _, summary = prepare()
        self.assertEqual(summary["usage_path"], os.environ["DBAGG_USAGE_PATH"])

    def test_no_enabled_routes_rejects_a_misleading_jev_pilot(self):
        self.values["SQL_ALLOWED_TABLES"] = "proadel.catalogo_bancos_data_v"
        self.write_env()
        with self.assertRaisesRegex(ValueError, "no habilita rutas"):
            prepare()

    def test_pilot_config_does_not_change_default_config_behavior(self):
        config = load_llm_config(root=self.root, environ={})
        self.assertFalse(config.modular.enabled)

    def test_jev_failure_uses_one_mini_routing_fallback_then_extraction(self):
        from unittest.mock import Mock
        from tests.unit.test_modular_context import reply

        settings, _ = prepare()
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Demo", 100)
        mini = Mock()
        mini.complete.return_value = reply("select_route", {"route": "clientes.saldo"})
        assistant = Assistant(settings, db=db, provider=mini)
        self.addCleanup(assistant.close)
        assistant.router.close()
        failed = Mock()
        failed.decide.side_effect = RuntimeError("synthetic router unavailable")
        assistant.router = failed
        assistant.extraction_provider.close()
        extraction = Mock()
        extraction.profile = settings.llm.profile
        extraction.complete.return_value = reply(
            "consultar_negocio", {"operation": "saldo", "customer": "Cliente Demo"}
        )
        assistant.extraction_provider = extraction
        self.assertIn("100.00", assistant.answer("Saldo de Cliente Demo"))
        failed.decide.assert_called_once()
        mini.complete.assert_called_once()
        extraction.complete.assert_called_once()
        self.assertTrue(
            any(
                e.get("event") == "routing" and e.get("fallback")
                for e in assistant.last_trace["events"]
            )
        )

    def test_signed_whatsapp_message_reaches_jev_mini_business_and_usage(self):
        settings, summary = prepare()
        db = SyntheticDatabase(check_same_thread=False)
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Demo", 100)
        request_paths = []

        def jev_response(request):
            request_paths.append(request.url.path)
            criteria = json.loads(request.content)["questions"]["route"]["criteria"]
            return httpx.Response(
                200,
                json={
                    "model": "jev-1.14.0",
                    "answers": {
                        "route": {
                            "type": "choice",
                            "choice": "clientes.saldo",
                            "confidence": 1,
                            "probabilities": {
                                name: float(name == "clientes.saldo") for name in criteria
                            },
                        }
                    },
                    "usage": {"input_tokens": 80, "output_tokens": 2},
                },
            )

        response = openai_response(
            [
                call(
                    "consultar_negocio",
                    json.dumps({"operation": "saldo", "customer": "Cliente Demo"}),
                )
            ]
        )
        response["usage"] = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        model_http = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
        )
        self.addCleanup(model_http.close)
        sdk = OpenAI(api_key="synthetic", http_client=model_http, max_retries=0)
        self.addCleanup(sdk.close)
        provider = OpenAICompatibleProvider(settings.llm.profile, "unused", client=sdk)
        assistant = Assistant(settings, db=db, provider=provider)
        self.addCleanup(assistant.close)
        assistant.extraction_provider.close()
        assistant.extraction_provider = OpenAICompatibleProvider(
            settings.llm.profiles[settings.llm.modular.extraction_profile], "unused", client=sdk
        )
        assistant.router.close()
        jev_http = httpx.Client(transport=httpx.MockTransport(jev_response))
        self.addCleanup(jev_http.close)
        assistant.router = JevRouter(settings.llm.modular, "synthetic", client=jev_http)
        delivered = []
        app = create_app(settings, assistant, lambda sender, text: delivered.append(text))
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
                                        "id": "local-pilot-test",
                                        "from": "5215555555555",
                                        "type": "text",
                                        "text": {"body": "Saldo de Cliente Demo"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ],
        }
        body = json.dumps(payload).encode()
        signature = (
            "sha256=" + hmac.new(settings.app_secret.encode(), body, hashlib.sha256).hexdigest()
        )
        with TestClient(app) as client:
            self.assertEqual(
                client.post(
                    "/webhook", content=body, headers={"x-hub-signature-256": signature}
                ).status_code,
                200,
            )
        self.assertEqual(request_paths, ["/v1/systemone"])
        self.assertIn("100.00", delivered[0])
        events = [
            json.loads(line)
            for line in Path(summary["usage_path"]).read_text(encoding="utf-8").splitlines()
        ]
        usage = [event for event in events if event["event"] == "usage"]
        self.assertEqual([event["provider"] for event in usage], ["jev", "openai"])
        self.assertEqual(usage[1]["task"], "extraction")
        self.assertEqual([event["total_tokens"] for event in usage], [82, 110])
        self.assertEqual(len({event["request_id"] for event in events}), 1)
        self.assertNotIn("private-", Path(summary["usage_path"]).read_text(encoding="utf-8"))
