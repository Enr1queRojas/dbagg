"""OpenAI-compatible APIs and Anthropic Messages, normalized before agent execution."""

import copy
import json
import time

import httpx
from openai import OpenAI

from dbagg.llm.types import ModelProtocolError, ModelReply, ToolCall, validate_calls


class OpenAICompatibleProvider:
    def __init__(self, profile, api_key, client=None):
        self.profile = profile
        self.owns_client = client is None
        self.client = (
            client
            if client is not None
            else OpenAI(
                api_key=api_key,
                base_url=profile.base_url,
                timeout=profile.timeout_seconds,
                max_retries=profile.max_retries,
            )
        )

    def close(self):
        if self.owns_client:
            self.client.close()

    def complete(self, messages, tools):
        profile = self.profile
        options = {
            "model": profile.model,
            "messages": copy.deepcopy(messages),
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            profile.token_parameter: profile.max_output_tokens,
        }
        if profile.temperature is not None:
            options["temperature"] = profile.temperature
        if profile.extra_body:
            options["extra_body"] = copy.deepcopy(profile.extra_body)
        response = self.client.chat.completions.create(**options)
        if not response.choices or response.choices[0].finish_reason not in {"stop", "tool_calls"}:
            raise ModelProtocolError(
                "El proveedor no terminó una respuesta válida; no se ejecutaron herramientas."
            )
        message = response.choices[0].message
        calls = tuple(
            ToolCall(c.id, c.function.name, c.function.arguments)
            for c in (message.tool_calls or [])
        )
        validate_calls(calls)
        history = {"role": "assistant", "content": message.content}
        if calls:
            # Preserve opaque Gemini thought signatures and other tool-call extensions.
            history["tool_calls"] = [
                c.model_dump(exclude_none=True)
                if hasattr(c, "model_dump")
                else {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments},
                }
                for c in message.tool_calls
            ]
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is not None:
            history["reasoning_content"] = reasoning
        return ModelReply(message.content, calls, history)


def anthropic_messages(messages):
    system, converted = [], []
    for message in messages:
        role = message["role"]
        if role == "system":
            system.append(message["content"])
            continue
        if role == "tool":
            role = "user"
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message["content"],
                }
            ]
        elif role == "assistant" and "_anthropic_content" in message:
            # Signed thinking blocks must be returned unchanged to Claude.
            blocks = copy.deepcopy(message["_anthropic_content"])
        elif role in {"user", "assistant"}:
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
        else:
            raise ModelProtocolError("Rol de mensaje no soportado.")
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return "\n\n".join(system), converted


class AnthropicProvider:
    def __init__(self, profile, api_key, client=None):
        self.profile = profile
        self.api_key = api_key
        self.owns_client = client is None
        self.client = (
            client
            if client is not None
            else httpx.Client(
                timeout=profile.timeout_seconds,
                follow_redirects=False,
            )
        )

    def close(self):
        if self.owns_client:
            self.client.close()

    def complete(self, messages, tools):
        system, converted = anthropic_messages(messages)
        profile = self.profile
        payload = {
            "model": profile.model,
            "max_tokens": profile.max_output_tokens,
            "system": system,
            "messages": converted,
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        }
        if profile.temperature is not None:
            payload["temperature"] = profile.temperature
        payload.update(copy.deepcopy(profile.extra_body))
        for attempt in range(profile.max_retries + 1):
            try:
                response = self.client.post(
                    profile.base_url.rstrip("/") + "/messages",
                    json=payload,
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt == profile.max_retries:
                    break
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == profile.max_retries:
                    raise
            time.sleep(min(0.5 * 2**attempt, 2))
        response.raise_for_status()
        try:
            data = response.json()
            if data.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence"}:
                raise ModelProtocolError(
                    "Claude no terminó una respuesta válida; no se ejecutaron herramientas."
                )
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise ModelProtocolError("Claude devolvió contenido inválido.")
            calls, texts = [], []
            for block in blocks:
                if block["type"] == "tool_use":
                    if not isinstance(block["input"], dict):
                        raise ModelProtocolError("Claude devolvió argumentos inválidos.")
                    calls.append(
                        ToolCall(
                            block["id"],
                            block["name"],
                            json.dumps(block["input"], ensure_ascii=False),
                        )
                    )
                elif block["type"] == "text":
                    texts.append(block["text"])
                elif block["type"] not in {"thinking", "redacted_thinking"}:
                    raise ModelProtocolError("Claude devolvió un bloque no soportado.")
            validate_calls(calls)
            content = "\n".join(texts) or None
            history = {
                "role": "assistant",
                "content": content,
                "_anthropic_content": copy.deepcopy(blocks),
            }
            return ModelReply(content, tuple(calls), history)
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ModelProtocolError("Claude devolvió una respuesta inválida.") from None


def create_provider(profile, api_key, client=None):
    if profile.provider == "anthropic":
        return AnthropicProvider(profile, api_key, client=client)
    if profile.provider in {"openai", "glm", "gemini", "openai_compatible"}:
        return OpenAICompatibleProvider(profile, api_key, client=client)
    raise ValueError("Proveedor LLM no soportado.")
