"""TypeSafe Choice HTTP adapter, verified against official API docs 2026-11-10.

https://docs.typesafe.ai/api/system-one.md
Confidence remains provider-specific. There are no automatic retries; the caller
has one bounded reference fallback, including when the API key is unavailable.
"""

import math

import httpx

from dbagg.routing.contracts import RoutingDecision
from dbagg.routing.registry import route_options
from dbagg.routing.service import INSTRUCTIONS, routing_state


class JevRouter:
    def __init__(self, config, api_key, client=None):
        self.config = config
        self.api_key = api_key
        self.owns_client = client is None
        self.client = (
            client
            if client is not None
            else httpx.Client(
                timeout=config.timeout_seconds,
                follow_redirects=False,
            )
        )

    def close(self):
        if self.owns_client:
            self.client.close()

    def decide(self, question, history, routes):
        if not self.api_key:
            raise ValueError("Credencial Jev no disponible.")
        options = route_options(routes)
        response = self.client.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": "Bearer " + self.api_key},
            json={
                "model": self.config.jev_model,
                "state": routing_state(question, history),
                "questions": {
                    "route": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": options}
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        answer = data["answers"]["route"]
        route = answer["choice"]
        confidence = answer.get("confidence")
        probabilities = answer["probabilities"]

        def probability(value):
            return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1

        if (
            answer.get("type") != "choice"
            or route not in options
            or (confidence is not None and not probability(confidence))
            or not isinstance(probabilities, dict)
            or set(probabilities) != set(options)
            or any(not probability(p) for p in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1, abs_tol=0.01)
        ):
            raise ValueError("Respuesta Jev inválida.")
        return RoutingDecision(
            route, "jev", confidence, probabilities, data.get("model"), data.get("usage", {})
        )
