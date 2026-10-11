"""HTTP contract tests; they neither call live providers nor require real API keys."""

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from openai import OpenAI

from dbagg.agent.prompts import tool_definitions
from dbagg.llm.config import ModelProfile
from dbagg.llm.providers import AnthropicProvider, OpenAICompatibleProvider, create_provider
from dbagg.llm.types import ModelProtocolError


def openai_response(calls=None, finish=None, **extras):
    return {
        "id": "demo-response",
        "created": 1,
        "model": "demo",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish or ("tool_calls" if calls else "stop"),
                "message": {
                    "role": "assistant",
                    "content": None if calls else "Texto sin evidencia",
                    "tool_calls": calls,
                    **extras,
                },
            }
        ],
    }


def call(name="describir_tablas", arguments='{"tables":["proadel.demo"]}', **extra):
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
        **extra,
    }


class ProviderTests(unittest.TestCase):
    def compatible(self, profile, responses):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=responses.pop(0))

        http = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(http.close)
        sdk = OpenAI(
            api_key="synthetic-key", base_url=profile.base_url, http_client=http, max_retries=0
        )
        self.addCleanup(sdk.close)
        return OpenAICompatibleProvider(profile, "unused", client=sdk), requests

    def test_compatible_providers_preserve_tool_calls_and_selected_parameters(self):
        for name in ("openai", "glm", "gemini", "openai_compatible"):
            with self.subTest(provider=name):
                profile = ModelProfile(
                    provider=name,
                    model="chosen-model",
                    base_url="https://example.test/v1",
                    max_output_tokens=2345,
                    token_parameter="max_completion_tokens",
                )
                provider, requests = self.compatible(profile, [openai_response([call()])])
                reply = provider.complete([{"role": "user", "content": "hola"}], tool_definitions())
                body = json.loads(requests[0].content)
                self.assertEqual(str(requests[0].url), "https://example.test/v1/chat/completions")
                self.assertEqual(body["model"], "chosen-model")
                self.assertEqual(body["max_completion_tokens"], 2345)
                self.assertNotIn("max_tokens", body)
                self.assertNotIn("temperature", body)
                self.assertFalse(body["parallel_tool_calls"])
                self.assertEqual(reply.tool_calls[0].name, "describir_tablas")

    def test_gemini_signature_survives_tool_result_roundtrip(self):
        tool = call(extra_content={"google": {"thought_signature": "synthetic-signature"}})
        provider, requests = self.compatible(
            ModelProfile(provider="gemini"), [openai_response([tool]), openai_response()]
        )
        messages = [{"role": "user", "content": "consulta"}]
        reply = provider.complete(messages, tool_definitions())
        messages.extend(
            [
                reply.history_message,
                {"role": "tool", "tool_call_id": "call-1", "content": '{"columns":[]}'},
            ]
        )
        provider.complete(messages, tool_definitions())
        sent = json.loads(requests[1].content)["messages"][1]["tool_calls"][0]
        self.assertEqual(sent["extra_content"], tool["extra_content"])
        self.assertEqual(messages[1]["tool_calls"][0]["extra_content"], tool["extra_content"])

    def test_glm_reasoning_and_extra_body_survive_roundtrip(self):
        profile = ModelProfile(provider="glm", extra_body={"thinking": {"type": "enabled"}})
        provider, requests = self.compatible(
            profile,
            [openai_response([call()], reasoning_content="synthetic-reasoning"), openai_response()],
        )
        reply = provider.complete([{"role": "user", "content": "consulta"}], tool_definitions())
        provider.complete(
            [reply.history_message, {"role": "tool", "tool_call_id": "call-1", "content": "{}"}],
            tool_definitions(),
        )
        self.assertEqual(
            json.loads(requests[1].content)["messages"][0]["reasoning_content"],
            "synthetic-reasoning",
        )
        self.assertEqual(json.loads(requests[0].content)["thinking"], {"type": "enabled"})

    def test_truncated_or_duplicate_tool_calls_are_rejected(self):
        for response in (
            openai_response([call()], finish="length"),
            openai_response([call(), call()]),
        ):
            provider, _ = self.compatible(ModelProfile(), [response])
            with self.assertRaises(ModelProtocolError):
                provider.complete([{"role": "user", "content": "consulta"}], tool_definitions())

    def test_anthropic_tool_result_and_signed_thinking_roundtrip(self):
        requests = []
        blocks = [
            {"type": "thinking", "thinking": "synthetic", "signature": "synthetic-signature"},
            {
                "type": "tool_use",
                "id": "tool-1",
                "name": "describir_tablas",
                "input": {"tables": ["proadel.demo"]},
            },
        ]
        responses = [
            {"stop_reason": "tool_use", "content": blocks},
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": "listo"}]},
        ]

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=responses.pop(0))

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            provider = AnthropicProvider(
                ModelProfile(
                    provider="anthropic", base_url="https://example.test/v1", model="claude-test"
                ),
                "synthetic-key",
                client,
            )
            messages = [
                {"role": "system", "content": "reglas"},
                {"role": "user", "content": "hola"},
            ]
            reply = provider.complete(messages, tool_definitions())
            messages.extend(
                [
                    reply.history_message,
                    {"role": "tool", "tool_call_id": "tool-1", "content": '{"data":[]}'},
                ]
            )
            provider.complete(messages, tool_definitions())
        body = json.loads(requests[1].content)
        self.assertEqual(str(requests[0].url), "https://example.test/v1/messages")
        self.assertEqual(requests[0].headers["x-api-key"], "synthetic-key")
        self.assertNotIn("authorization", requests[0].headers)
        self.assertEqual(body["system"], "reglas")
        self.assertEqual(body["messages"][1]["content"], blocks)
        self.assertEqual(body["messages"][2]["content"][0]["tool_use_id"], "tool-1")
        self.assertIn("input_schema", body["tools"][0])
        self.assertEqual(reply.tool_calls[0].name, "describir_tablas")

    def test_anthropic_retries_only_transient_errors_and_stops_at_bound(self):
        for statuses, expected in (([429, 200], 2), ([500, 500], 2), ([401], 1)):
            requests = []

            def handle(request):
                requests.append(request)
                return httpx.Response(
                    statuses[len(requests) - 1],
                    json={"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]},
                )

            with (
                httpx.Client(transport=httpx.MockTransport(handle)) as client,
                patch("dbagg.llm.providers.time.sleep"),
            ):
                provider = AnthropicProvider(
                    ModelProfile(provider="anthropic", max_retries=1), "synthetic", client
                )
                if statuses[-1] == 200:
                    provider.complete([{"role": "user", "content": "hola"}], tool_definitions())
                else:
                    with self.assertRaises(httpx.HTTPStatusError):
                        provider.complete([{"role": "user", "content": "hola"}], tool_definitions())
            self.assertEqual(len(requests), expected)

    def test_anthropic_max_tokens_prevents_execution_of_partial_tools(self):
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "stop_reason": "max_tokens",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool-1",
                                "name": "consultar_negocio",
                                "input": {"operation": "saldo"},
                            }
                        ],
                    },
                )
            )
        ) as client:
            provider = AnthropicProvider(ModelProfile(provider="anthropic"), "synthetic", client)
            with self.assertRaises(ModelProtocolError):
                provider.complete([{"role": "user", "content": "hola"}], tool_definitions())

    def test_factory_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            create_provider(replace(ModelProfile(), provider="unknown"), "synthetic")
