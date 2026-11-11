"""Content-free usage events. Provider counts are authoritative; missing counts stay null."""

import contextvars
import functools
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dbagg.paths import project_root

_trace = contextvars.ContextVar("usage_trace", default=None)
_call = contextvars.ContextVar("usage_call", default=None)
_task = contextvars.ContextVar("usage_task", default=None)
_lock = threading.Lock()
logger = logging.getLogger("uvicorn.error")


def _dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value if isinstance(value, dict) else {}


def normalize_usage(raw, provider):
    raw = _dict(raw)

    def count(value):
        return value if type(value) is int and value >= 0 else None

    input_tokens = count(raw.get("input_tokens", raw.get("prompt_tokens")))
    output_tokens = count(raw.get("output_tokens", raw.get("completion_tokens")))
    cached = count(_dict(raw.get("prompt_tokens_details")).get("cached_tokens"))
    created = count(raw.get("cache_creation_input_tokens"))
    if provider == "anthropic":
        cached = count(raw.get("cache_read_input_tokens"))
        if input_tokens is not None:
            input_tokens += (cached or 0) + (created or 0)
    total = count(raw.get("total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return {
        "usage_available": bool(raw),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total,
        "cached_input_tokens": cached,
        "cache_creation_input_tokens": created,
        "reasoning_tokens": count(
            _dict(raw.get("completion_tokens_details")).get("reasoning_tokens")
        ),
        "audio_input_tokens": count(_dict(raw.get("input_token_details")).get("audio_tokens")),
        "audio_seconds": raw.get("seconds")
        if isinstance(raw.get("seconds"), (int, float))
        else None,
    }


def emit(event):
    event = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    trace = _trace.get()
    if trace is not None:
        event["request_id"] = trace["request_id"]
        event["sequence"] = len(trace["events"]) + 1
        trace["events"].append(event)
    try:
        path = Path(os.getenv("DBAGG_USAGE_PATH", str(project_root() / "data" / "usage.jsonl")))
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("dbagg stage=usage_write_failed")


@contextmanager
def usage_scope():
    existing = _trace.get()
    if existing is not None:
        yield existing
        return
    trace = {"request_id": uuid4().hex, "events": [], "started": time.perf_counter()}
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def stage(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with usage_scope() as trace:
                started = time.perf_counter()
                status = "ok"
                try:
                    return function(*args, **kwargs)
                except Exception:
                    status = "error"
                    raise
                finally:
                    emit(
                        {
                            "event": "stage",
                            "stage": name,
                            "status": status,
                            "start_ms": round((started - trace["started"]) * 1000, 3),
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        }
                    )
                    if name == "answer" and isinstance(getattr(args[0], "last_trace", None), dict):
                        calls = [e for e in trace["events"] if e["event"] == "usage"]
                        args[0].last_trace["usage"] = {
                            "request_id": trace["request_id"],
                            "calls": len(calls),
                            "measured_calls": sum(e["total_tokens"] is not None for e in calls),
                            "known_total_tokens": sum(e["total_tokens"] or 0 for e in calls),
                        }

        return wrapped

    return decorate


def record_response(raw):
    call = _call.get()
    if call is not None:
        call.update(normalize_usage(raw, call["provider"]))


@contextmanager
def task_scope(name):
    token = _task.set(name)
    try:
        yield
    finally:
        _task.reset(token)


def measured(kind):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            profile = getattr(self, "profile", None) or self.config
            provider = getattr(profile, "provider", "openai_compatible")
            event = {
                "event": "usage",
                "stage": kind,
                "provider": provider,
                "model": profile.model,
                **normalize_usage(None, provider),
            }
            event["task"] = _task.get() or ("routing" if provider == "jev" else kind)
            if kind == "model":
                messages = args[0] if args else kwargs["messages"]
                tools = args[1] if len(args) > 1 else kwargs["tools"]
                previous = [
                    m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")
                ]
                event["round"] = len(previous) + 1
                event["require_tool"] = bool(
                    kwargs.get("require_tool", args[2] if len(args) > 2 else False)
                )
                event["message_chars_by_role"] = {
                    role: sum(
                        len(json.dumps(m, ensure_ascii=False))
                        for m in messages
                        if m.get("role") == role
                    )
                    for role in ("system", "user", "assistant", "tool")
                }
                event["tools_chars"] = len(json.dumps(tools, ensure_ascii=False))
                system = next(
                    (m.get("content", "") for m in messages if m.get("role") == "system"), ""
                )
                if isinstance(system, str):
                    parts = system.split("\nObjetos autorizados: ", 1)
                    sections = {"instructions": len(parts[0])}
                    if len(parts) == 2:
                        catalog, _, rest = parts[1].partition("\nContexto de negocio: ")
                        context, _, calendar = rest.partition(
                            "\nCalendario de la zona horaria del negocio: "
                        )
                        sections.update(
                            catalog=len(catalog),
                            business_context=len(context),
                            calendar=len(calendar),
                        )
                    event["system_chars_by_section"] = sections
                event["after_tool"] = (
                    previous[-1]["tool_calls"][0]["function"]["name"] if previous else None
                )
            with usage_scope() as trace:
                token = _call.set(event)
                started = time.perf_counter()
                try:
                    result = function(self, *args, **kwargs)
                    event["status"] = "ok"
                    return result
                except Exception as exc:
                    event.update(status="error", error_type=type(exc).__name__)
                    raise
                finally:
                    event["start_ms"] = round((started - trace["started"]) * 1000, 3)
                    event["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    _call.reset(token)
                    emit(event)
                    logger.info(
                        "dbagg stage=provider_usage request_id=%s task=%s provider=%s model=%s "
                        "status=%s input_tokens=%s output_tokens=%s cached_input_tokens=%s "
                        "total_tokens=%s duration_ms=%s",
                        trace["request_id"],
                        event["task"],
                        event["provider"],
                        event["model"],
                        event["status"],
                        event["input_tokens"],
                        event["output_tokens"],
                        event["cached_input_tokens"],
                        event["total_tokens"],
                        event["duration_ms"],
                    )

        return wrapped

    return decorate
