"""Versioned public list-price estimates; unknown usage never means zero cost."""

RATES_DATE = "2026-11-10"
# USD per million tokens, standard API. Cache is a subset of input for OpenAI.
# https://developers.openai.com/api/docs/models/gpt-4.1-mini
# https://developers.openai.com/api/docs/models/gpt-4.1-nano
# https://docs.typesafe.ai/models.md
RATES = {
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "jev-1.14.0": (0.042, 0.042, 0.0),
}


def estimate_event(event):
    usage = event.get("usage")
    rates = RATES.get(event.get("model"))
    if not isinstance(usage, dict) or rates is None:
        return None
    incoming = usage.get("prompt_tokens", usage.get("input_tokens"))
    outgoing = usage.get("completion_tokens", usage.get("output_tokens"))
    if event.get("provider") == "jev":
        cached = 0  # Jev lists a single input class and free output.
    else:
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
    if any(type(v) is not int or v < 0 for v in (incoming, outgoing, cached)):
        return None
    if cached > incoming:
        return None
    normal_rate, cached_rate, output_rate = rates
    return (
        (incoming - cached) * normal_rate + cached * cached_rate + outgoing * output_rate
    ) / 1_000_000


def estimate_events(events):
    costs = [estimate_event(e) for e in events if e.get("event") == "usage"]
    return {
        "usd_known": sum(c for c in costs if c is not None),
        "unknown_attempts": sum(c is None for c in costs),
        "complete": all(c is not None for c in costs),
        "rates_date": RATES_DATE,
    }
