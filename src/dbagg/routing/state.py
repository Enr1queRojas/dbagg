"""Explicit volatile continuity. Identity comes only from verified business results.

Kept separate from usage telemetry; the full existing history is still retained.
"""

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class ConversationState:
    version: int = 1
    previous_route: str | None = None
    selected_customer_id: str | None = None
    entity: str | None = None
    period: str | None = None
    start: str | None = None
    end: str | None = None
    pending_clarification: str | None = None

    def for_route(self, route):
        previous_domain = (self.previous_route or "").split(".")[0]
        domain = route.split(".")[0]
        if previous_domain != domain:
            return ConversationState(previous_route=route)
        return replace(self, previous_route=route)

    def after_result(self, metadata, status):
        if metadata.get("entity") in {"cliente", "proveedor"} and metadata.get("code"):
            return replace(
                self,
                selected_customer_id=metadata["code"],
                entity=metadata["entity"],
                period=metadata.get("period"),
                start=metadata.get("start"),
                end=metadata.get("end"),
                pending_clarification=None,
            )
        if status == "needs_clarification":
            return replace(
                self, selected_customer_id=None, entity=None, pending_clarification="cliente"
            )
        return replace(
            self,
            selected_customer_id=None,
            entity=None,
            period=None,
            start=None,
            end=None,
            pending_clarification=None,
        )

    def payload(self):
        return asdict(self)
