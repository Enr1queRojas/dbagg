"""The agent consumes these types, independently of an SDK or HTTP provider."""

from dataclasses import dataclass
from typing import Protocol


class ModelProtocolError(ValueError):
    """Malformed, truncated or unsupported model response; no tool should execute."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelReply:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    history_message: dict


class ModelProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...
    def close(self) -> None: ...


def validate_calls(calls):
    if len(calls) > 16 or len({c.id for c in calls}) != len(calls):
        raise ModelProtocolError(
            "El proveedor devolvió llamadas duplicadas o demasiadas herramientas."
        )
    for call in calls:
        if (
            not isinstance(call.id, str)
            or not 1 <= len(call.id) <= 200
            or not isinstance(call.name, str)
            or not 1 <= len(call.name) <= 100
            or not isinstance(call.arguments, str)
            or len(call.arguments) > 64000
        ):
            raise ModelProtocolError("El proveedor devolvió una llamada de herramienta inválida.")
