"""Select complete versioned modules; never silently trim mandatory rules."""

import hashlib
import json
from dataclasses import dataclass

from dbagg.agent.prompt_files import PromptBundle, TEMPLATES, _read, operations_text
from dbagg.agent.prompts import tool_definitions


WRONG_ROUTE_REASON = "otra_operacion"


@dataclass(frozen=True)
class SelectedContext:
    bundle: PromptBundle
    context: str
    schema: str
    tools: list[dict]
    modules: tuple[str, ...]
    tables: frozenset[str]


def select_context(route, context, bundle, catalog, tables):
    """Prompt, data and tools for one route, or for several routes of the same domain.

    With several routes the prompt carries core, domain and each candidate operation module;
    the operation enum is exactly those operations; tables and topics are the union. One route
    gives exactly the single-route result (same fingerprint).
    """
    routes = list(route) if isinstance(route, (list, tuple)) else [route]
    if not routes or len({r.domain for r in routes}) != 1:
        raise ValueError("Las rutas candidatas deben ser de un mismo dominio.")
    if len({r.name for r in routes}) != len(routes):
        raise ValueError("Rutas candidatas repetidas.")
    domain = "customers" if routes[0].domain == "clientes" else "expenses"
    operations = [r.operation for r in routes if r.operation]
    if operations and len(operations) != len(routes):
        raise ValueError("No se mezclan operaciones con exploración.")
    shared = tuple(dict.fromkeys(module for r in routes for module in r.modules))
    modules = (
        ("core",)
        + shared
        + ("domains/" + domain,)
        + tuple("operations/" + op for op in operations)
        + (("shared/wrong_route",) if operations else ())
    )
    system = "\n".join(_read(TEMPLATES / (name + ".md"), 26000) for name in modules)
    if operations:
        # The prompt names exactly the operations this turn's enum offers.
        system = operations_text(system, operations)
    # Unknown/local top-level rules are preserved, not silently discarded.
    data = json.loads(context)
    topics = data.get("topics", {})
    known_topics = {
        "customer_balance",
        "customer_statement",
        "collections",
        "supplier_balance",
        "supplier_payments",
        "sales",
        "inventory",
        "producers",
        "bank_accounts",
        "suppliers_and_purchases",
        "risk",
    }
    wanted = {topic for r in routes for topic in r.topics}
    # Explicitly unknown topics may contain local mandatory guidance. Keep them.
    data["topics"] = {k: v for k, v in topics.items() if k in wanted or k not in known_topics}
    tables = frozenset().union(*(r.tables for r in routes)) & catalog.restrict(tables)
    schema = json.dumps(catalog.cards(tables), ensure_ascii=False)
    selected = json.dumps(data, ensure_ascii=False)
    fingerprint = hashlib.sha256((system + selected + schema).encode()).hexdigest()[:16]
    modular_bundle = PromptBundle(system, bundle.descriptions, "modular-v1:" + fingerprint)
    allowed_tools = {tool for r in routes for tool in r.tools}
    tools = [
        t
        for t in tool_definitions(modular_bundle, suppliers=True)
        if t["function"]["name"] in allowed_tools
    ]
    for tool in tools:
        if tool["function"]["name"] == "pedir_aclaracion":
            # Modular only: the route was wrong; the service retries once with the full prompt.
            tool["function"]["parameters"]["properties"]["reason"]["enum"].append(
                WRONG_ROUTE_REASON
            )
    if operations:
        properties = tools[0]["function"]["parameters"]["properties"]
        properties["operation"]["enum"] = operations
        # No products, banks or comparisons in a customer operation.
        for name in ("producto", "comparar", "banco"):
            properties.pop(name, None)
    return SelectedContext(modular_bundle, selected, schema, tools, modules, tables)


def validate_tool_call(call, tools):
    """Validate the selected closed contract before any database or business call."""
    definitions = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    if call.name not in definitions:
        raise ValueError("Herramienta fuera de la ruta seleccionada.")
    arguments = json.loads(call.arguments)
    contract = definitions[call.name]
    if not isinstance(arguments, dict):
        raise ValueError("Los argumentos deben ser un objeto JSON.")
    properties = contract["properties"]
    if set(arguments) - set(properties) or not set(contract["required"]) <= set(arguments):
        raise ValueError("Parámetros fuera del contrato seleccionado.")
    types = {"string": str, "boolean": bool, "integer": int, "array": list}
    for name, value in arguments.items():
        spec = properties[name]
        if type(value) is not types[spec["type"]]:
            raise ValueError("Tipo de parámetro inválido.")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("Valor fuera del contrato seleccionado.")
        if type(value) is int and not spec.get("minimum", value) <= value <= spec.get(
            "maximum", value
        ):
            raise ValueError("Cantidad fuera del contrato seleccionado.")
        if isinstance(value, list) and (
            not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", len(value))
            or any(not isinstance(item, str) for item in value)
        ):
            raise ValueError("Lista fuera del contrato seleccionado.")
    return arguments
