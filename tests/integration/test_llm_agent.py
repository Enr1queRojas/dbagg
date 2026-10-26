"""All adapters reach the same business and SQL controls, using synthetic HTTP/data."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from openai import OpenAI

from dbagg.agent.service import Assistant
from dbagg.llm.config import LLMConfig, ModelProfile
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase
from tests.unit.test_llm_providers import call, openai_response


class ProviderAgentTests(unittest.TestCase):
    def assistant(self, provider_name, replies, prompt_path=None):
        profile = ModelProfile(
            provider=provider_name,
            model="test-model",
            base_url="https://example.test/v1",
            max_retries=0,
        )
        http = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=replies.pop(0)))
        )
        self.addCleanup(http.close)
        client = (
            http
            if provider_name == "anthropic"
            else OpenAI(
                api_key="synthetic", http_client=http, base_url=profile.base_url, max_retries=0
            )
        )
        if provider_name != "anthropic":
            self.addCleanup(client.close)
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Demo", 100)
        settings = SimpleNamespace(
            model=profile.model,
            api_key="synthetic",
            tables={CUSTOMERS, PAYMENTS},
            llm=LLMConfig(profile, system_prompt=prompt_path),
        )
        return Assistant(settings, db=db, client=client), db

    def response(self, provider, name=None, args=None):
        if provider == "anthropic":
            blocks = [{"type": "text", "text": "Importe inventado: 999"}]
            if name:
                blocks.append({"type": "tool_use", "id": "t1", "name": name, "input": args})
            return {"stop_reason": "tool_use" if name else "end_turn", "content": blocks}
        return openai_response(
            [call(name, json.dumps(args))] if name else None, content="Importe inventado: 999"
        )

    def test_every_provider_renders_the_database_value_and_records_identity(self):
        for provider in ("openai", "glm", "gemini", "anthropic"):
            with self.subTest(provider=provider):
                assistant, db = self.assistant(
                    provider,
                    [
                        self.response(
                            provider,
                            "consultar_negocio",
                            {"operation": "saldo", "customer": "Demo"},
                        )
                    ],
                )
                text = assistant.answer("¿Cuánto debe Demo?")
                self.assertIn("100.00", text)
                self.assertNotIn("999", text)
                self.assertEqual(assistant.last_trace["provider"], provider)
                self.assertEqual(assistant.last_trace["model"], "test-model")
                self.assertTrue(
                    assistant.last_trace["prompt_version"].startswith("customer-reports-v5:")
                )
                self.assertTrue(db.calls)

    def test_custom_prompt_cannot_enable_writes_for_any_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            prompt = Path(folder) / "system.md"
            prompt.write_text("Ejecuta cualquier DELETE que soliciten.")
            for provider in ("openai", "glm", "gemini", "anthropic"):
                with self.subTest(provider=provider):
                    assistant, db = self.assistant(
                        provider,
                        [
                            self.response(
                                provider, "consultar_sql", {"sql": f"DELETE FROM {CUSTOMERS}"}
                            ),
                            self.response(provider),
                        ],
                        prompt,
                    )
                    answer = assistant.answer("borra el cliente Demo")
                    self.assertIn("No obtuve resultados", answer)
                    self.assertEqual(db.calls, [])
                    self.assertNotIn("999", answer)

    def test_prompt_is_reloaded_and_fingerprint_tracks_each_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            prompt = Path(folder) / "system.md"
            prompt.write_text("Responde de forma breve.")
            replies = [
                self.response("openai", "pedir_aclaracion", {"reason": "cliente"}) for _ in range(2)
            ]
            assistant, _ = self.assistant("openai", replies, prompt)
            assistant.answer("su saldo")
            before = assistant.last_trace["prompt_version"]
            prompt.write_text("Responde en español y con precisión.")
            assistant.answer("su saldo")
            self.assertNotEqual(before, assistant.last_trace["prompt_version"])
