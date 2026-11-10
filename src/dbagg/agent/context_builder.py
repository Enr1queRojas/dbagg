"""Select complete versioned modules; never silently trim mandatory rules."""

import hashlib
import json
from dataclasses import dataclass

from dbagg.agent.prompt_files import PromptBundle, TEMPLATES, _read
from dbagg.agent.prompts import tool_definitions


@dataclass(frozen=True)
class SelectedContext:
    bundle: PromptBundle
    context: str
    schema: str
    tools: list[dict]
    modules: tuple[str, ...]
    tables: frozenset[str]


def select_context(route, context, bundle, catalog, tables):
    domain = "customers" if route.domain == "clientes" else "expenses"
    modules = ("core", "domains/" + domain)
    if route.operation:
        modules += ("operations/" + route.operation,)
    system = "\n".join(_read(TEMPLATES / (name + ".md"), 26000) for name in modules)
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
    # Explicitly unknown topics may contain local mandatory guidance. Keep them.
    data["topics"] = {k: v for k, v in topics.items() if k in route.topics or k not in known_topics}
    tables = route.tables & catalog.restrict(tables)
    schema = json.dumps(catalog.cards(tables), ensure_ascii=False)
    selected = json.dumps(data, ensure_ascii=False)
    fingerprint = hashlib.sha256((system + selected + schema).encode()).hexdigest()[:16]
    modular_bundle = PromptBundle(system, bundle.descriptions, "modular-v1:" + fingerprint)
    tools = [
        t
        for t in tool_definitions(modular_bundle, suppliers=True)
        if t["function"]["name"] in route.tools
    ]
    if route.operation:
        properties = tools[0]["function"]["parameters"]["properties"]
        properties["operation"]["enum"] = [route.operation]
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
