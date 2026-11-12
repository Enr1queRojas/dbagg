"""Explicit volatile continuity. Identity comes only from verified business results.

Kept separate from usage telemetry; the full existing history is still retained.
"""

from dataclasses import asdict, dataclass, replace


DOMAIN_FAMILY = {"clientes": "personas", "proveedores": "personas"}


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
    # The operation waiting for that clarification, validated by the service.
    pending_operation: str | None = None

    def for_route(self, route):
        # Customers and suppliers are one family of people: a supplier follow-up after a
        # customer-route answer keeps the confirmed person.
        previous_domain = DOMAIN_FAMILY.get(*([(self.previous_route or "").split(".")[0]] * 2))
        domain = DOMAIN_FAMILY.get(*([route.split(".")[0]] * 2))
        if previous_domain != domain:
            return ConversationState(previous_route=route)
        return replace(self, previous_route=route)

    def after_result(self, metadata, status, pending=None):
        """``pending`` is the request the service validated before asking who was meant."""
        if metadata.get("entity") in {"cliente", "proveedor"} and metadata.get("code"):
            return replace(
                self,
                selected_customer_id=metadata["code"],
                entity=metadata["entity"],
                period=metadata.get("period"),
                start=metadata.get("start"),
                end=metadata.get("end"),
                pending_clarification=None,
                pending_operation=None,
            )
        if status == "needs_clarification":
            if pending:
                return replace(
                    self,
                    selected_customer_id=None,
                    entity=None,
                    pending_clarification="cliente",
                    pending_operation=pending.get("operation"),
                    period=pending.get("period"),
                    start=pending.get("start"),
                    end=pending.get("end"),
                )
            return replace(
                self,
                selected_customer_id=None,
                entity=None,
                pending_clarification="cliente",
                pending_operation=None,
            )
        return replace(
            self,
            selected_customer_id=None,
            entity=None,
            period=None,
            start=None,
            end=None,
            pending_clarification=None,
            pending_operation=None,
        )

    def payload(self):
        return asdict(self)
