"""Provider-neutral routing data, distinct from provider confidence semantics."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Route:
    name: str
    domain: str
    operation: str | None
    description: str
    topics: tuple[str, ...]
    tables: frozenset[str]
    tools: tuple[str, ...]
    executor: str
    version: str = "1"


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    provider: str
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    usage: dict = field(default_factory=dict)
