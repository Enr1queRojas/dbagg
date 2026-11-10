"""Usage must survive protocol errors, preserve unknowns and isolate concurrent messages."""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import OpenAI

from dbagg.llm.config import ModelProfile, TranscriptionConfig
from dbagg.llm.providers import AnthropicProvider, OpenAICompatibleProvider
from dbagg.llm.transcription import Transcriber
from dbagg.llm.types import ModelProtocolError
from dbagg.llm.usage import emit, measured, normalize_usage, record_response, usage_scope
from tests.unit.test_llm_providers import openai_response


class UsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "usage.jsonl"
        env = patch.dict(os.environ, {"DBAGG_USAGE_PATH": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def events(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def compatible(self, body):
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
        self.addCleanup(http.close)
        client = OpenAI(api_key="secret-never-log", http_client=http, max_retries=0)
        self.addCleanup(client.close)
        return OpenAICompatibleProvider(ModelProfile(), "unused", client=client)

    def test_cached_and_reasoning_are_subsets_not_double_counted(self):
        usage = normalize_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 70},
                "completion_tokens_details": {"reasoning_tokens": 10},
            },
            "openai",
        )
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(usage["cached_input_tokens"], 70)
        self.assertEqual(usage["reasoning_tokens"], 10)

    def test_anthropic_cache_counts_are_added_to_input(self):
        usage = normalize_usage(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 70,
                "cache_creation_input_tokens": 30,
            },
            "anthropic",
        )
        self.assertEqual(usage["input_tokens"], 200)
        self.assertEqual(usage["total_tokens"], 220)

    def test_missing_and_invalid_counts_are_unknown(self):
        for raw in (None, {}, {"prompt_tokens": -1, "completion_tokens": True}):
            self.assertIsNone(normalize_usage(raw, "openai")["total_tokens"])

    def test_protocol_failure_still_records_billed_usage_without_content(self):
        body = openai_response(finish="length")
        body["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        with self.assertRaises(ModelProtocolError):
            self.compatible(body).complete([{"role": "user", "content": "private-question"}], [])
        event = self.events()[0]
        self.assertEqual(event["total_tokens"], 120)
        self.assertEqual(event["status"], "error")
        self.assertNotIn("private-question", self.path.read_text())
        self.assertNotIn("secret-never-log", self.path.read_text())

    def test_anthropic_records_usage(self):
        body = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 5},
        }
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
        self.addCleanup(http.close)
        AnthropicProvider(ModelProfile(provider="anthropic"), "secret", client=http).complete(
            [], []
        )
        self.assertEqual(self.events()[0]["total_tokens"], 17)

    def test_transcription_tracks_audio_and_same_request(self):
        body = {
            "text": "private transcript",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 3,
                "total_tokens": 13,
                "input_token_details": {"audio_tokens": 8},
            },
        }
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        with usage_scope() as trace:
            text = Transcriber(TranscriptionConfig(), "secret", transport).transcribe(
                b"audio", "audio/ogg"
            )
            emit({"event": "stage", "stage": "delivery"})
        self.assertEqual(text, "private transcript")
        events = self.events()
        self.assertTrue(all(e["request_id"] == trace["request_id"] for e in events))
        self.assertEqual(events[0]["audio_input_tokens"], 8)
        self.assertNotIn("private transcript", self.path.read_text())

    def test_concurrent_calls_have_distinct_trace_ids(self):
        class Fake:
            profile = SimpleNamespace(provider="openai", model="test")

            @measured("model")
            def complete(self, messages, tools):
                record_response({"prompt_tokens": 1, "completion_tokens": 2})

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: Fake().complete([], []), range(12)))
        events = self.events()
        self.assertEqual(len({e["request_id"] for e in events}), 12)
        self.assertTrue(all(e["total_tokens"] == 3 for e in events))

    def test_write_failure_does_not_break_response(self):
        with patch("dbagg.llm.usage.Path.open", side_effect=OSError):
            self.compatible(openai_response()).complete([], [])


class ModularUsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "usage.jsonl"
        env = patch.dict(os.environ, {"DBAGG_USAGE_PATH": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_jev_and_model_share_request_and_preserve_task_and_usage(self):
        from dbagg.llm.config import ModularConfig
        from dbagg.llm.usage import task_scope
        from dbagg.routing.jev import JevRouter
        from dbagg.routing.registry import available_routes, route_options
        from dbagg.database.catalog import load_catalog

        routes = available_routes(load_catalog(), {"proadel.catalogo_clientes_data_v"})
        options = route_options(routes)
        body = {
            "model": "jev-1.14.0",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "clientes.saldo",
                    "confidence": 1,
                    "probabilities": {name: float(name == "clientes.saldo") for name in options},
                }
            },
            "usage": {"input_tokens": 80, "output_tokens": 2},
        }
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        )
        self.addCleanup(client.close)
        router = JevRouter(ModularConfig(), "private-key", client)

        class Extractor:
            profile = SimpleNamespace(provider="openai", model="test")

            @measured("model")
            def complete(self, messages, tools):
                record_response({"prompt_tokens": 100, "completion_tokens": 5})

        with usage_scope() as trace:
            router.decide("private question", [], routes)
            with task_scope("extraction"):
                Extractor().complete([], [])
        events = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["total_tokens"] for e in events], [82, 105])
        self.assertEqual([e["task"] for e in events], ["routing", "extraction"])
        self.assertEqual([e["sequence"] for e in events], [1, 2])
        self.assertTrue(all(e["request_id"] == trace["request_id"] for e in events))
        self.assertNotIn("private", self.path.read_text(encoding="utf-8"))

    def test_malformed_jev_response_keeps_usage_and_http_failure_keeps_unknown(self):
        from dbagg.llm.config import ModularConfig
        from dbagg.routing.jev import JevRouter
        from dbagg.routing.registry import available_routes
        from dbagg.database.catalog import load_catalog

        routes = available_routes(load_catalog(), {"proadel.catalogo_clientes_data_v"})
        responses = [
            httpx.Response(
                200,
                json={
                    "usage": {"input_tokens": 80, "output_tokens": 2},
                    "answers": {"route": {"choice": "not_registered", "probabilities": {}}},
                },
            ),
            httpx.Response(503),
        ]
        client = httpx.Client(transport=httpx.MockTransport(lambda r: responses.pop(0)))
        self.addCleanup(client.close)
        router = JevRouter(ModularConfig(), "secret", client)
        with self.assertRaises(ValueError):
            router.decide("private", [], routes)
        with self.assertRaises(httpx.HTTPStatusError):
            router.decide("private", [], routes)
        events = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["total_tokens"], 82)
        self.assertEqual(events[0]["status"], "error")
        self.assertIsNone(events[1]["total_tokens"])
        self.assertEqual(events[1]["status"], "error")
