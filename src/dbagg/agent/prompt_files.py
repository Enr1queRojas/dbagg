"""Load editable text without executing templates; fingerprint the actual instructions."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PROMPT_VERSION = "catalog-domains-v7"
TOOL_NAMES = frozenset(
    {"consultar_negocio", "pedir_aclaracion", "describir_tablas", "consultar_sql"}
)
TEMPLATES = Path(__file__).with_name("templates")
# Full-interpreter composition (system.md is generated, do not edit it by hand):
# every module once, from stable rules to examples. See scripts/build_system_prompt.py.
SYSTEM_MODULES = ("core", "shared/identity", "shared/time")
OPERATION_MODULES = (
    "saldo",
    "saldos_coincidencias",
    "contar_coincidencias",
    "notas_pendientes",
    "ranking_deuda",
    "pagos",
    "total_pagos",
    "ultimo_pago",
    "ultima_nota",
    "estado_cuenta",
)
DOMAIN_MODULES = ("products", "customers", "banks", "expenses")


@dataclass(frozen=True)
class PromptBundle:
    system: str
    descriptions: dict[str, str]
    version: str


def _read(path, limit):
    if not path.is_file() or path.stat().st_size > limit * 4:
        raise ValueError(
            "Prompt inexistente o demasiado grande; revisa las rutas de configuración."
        )
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        raise ValueError("El prompt debe estar codificado en UTF-8.") from None
    if not text.strip() or len(text) > limit:
        raise ValueError("Prompt vacío o demasiado grande.")
    return text


def load_prompt_bundle(config=None):
    system_path = getattr(config, "system_prompt", None) or TEMPLATES / "system.md"
    tools_path = getattr(config, "tool_descriptions", None) or TEMPLATES / "tools.json"
    system = _read(system_path, 26000)
    try:
        descriptions = json.loads(_read(tools_path, 8000))
    except json.JSONDecodeError:
        raise ValueError("Las descripciones de herramientas requieren JSON válido.") from None
    if (
        not isinstance(descriptions, dict)
        or set(descriptions) != TOOL_NAMES
        or any(
            not isinstance(v, str) or not v.strip() or len(v) > 1500 for v in descriptions.values()
        )
    ):
        raise ValueError("Los prompts solo pueden describir las cuatro herramientas autorizadas.")
    canonical = json.dumps(
        {"system": system, "tools": descriptions}, sort_keys=True, ensure_ascii=False
    )
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return PromptBundle(system, descriptions, f"{PROMPT_VERSION}:{fingerprint}")


def _module(name):
    return (TEMPLATES / (name + ".md")).read_text(encoding="utf-8-sig").strip("\n")


def compose_system_prompt():
    """The full prompt as the ordered composition of the modules the modular routes use.

    Operation modules lose their «Operación seleccionada» heading and are listed under one
    section, so ``system.md`` and the modular prompts share a single source of truth.
    """
    parts = [_module(name) for name in SYSTEM_MODULES]
    operations = ["# Operaciones verificadas: consultar_negocio"]
    for name in OPERATION_MODULES:
        body = _module("operations/" + name).split("\n", 1)[1]
        # Per-operation examples stay modular; the full prompt has examples.md.
        body = body.split("\nEjemplo (", 1)[0] if not body.startswith("Ejemplo (") else ""
        body = body.strip("\n")
        if not body.startswith("- "):
            body = f"- {name}: " + body.replace("\n", "\n  ")
        operations.append(body)
    parts.append("\n".join(operations))
    for name in DOMAIN_MODULES:
        text = _module("domains/" + name)
        if name == "customers":
            from dbagg.business.customers import OPERATIONS
            from dbagg.business.suppliers import SUPPLIER_ONLY_OPERATIONS

            text = operations_text(text, OPERATIONS, SUPPLIER_ONLY_OPERATIONS)
        parts.append(text)
    parts.append(_module("examples"))
    return "\n\n".join(parts) + "\n"


OPERATIONS_MARK = "[[operaciones]]"


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " y " + names[-1]


def operations_text(text, operations, supplier_only=()):
    """Mention only the operations of this prompt: the list is generated, never fixed text.

    Replaces the ``[[operaciones]]`` line and keeps «Ejemplo (op):» paragraphs only for the
    listed operations (a paragraph runs until the next line that starts a new statement).
    """
    operations = list(operations)
    if not operations:
        # Stable prefix: the list and examples move to the per-turn suffix.
        sentence = None
    else:
        sentence = (
            f"{_join(operations)} {'sirve' if len(operations) == 1 else 'sirven'} para ambos."
        )
    if sentence and supplier_only:
        sentence += f" {_join(supplier_only)} es solo de proveedores y admite períodos."
    kept, skipping = [], False
    for line in text.split("\n"):
        if line == OPERATIONS_MARK:
            if sentence:
                kept.append(sentence)
            skipping = False
            continue
        if line.startswith("Ejemplo ("):
            skipping = line[len("Ejemplo (") :].split(")", 1)[0] not in operations
        elif line.startswith(("«", "#", "Tras ", "Un ", "Agrega", "Estas", "No ")) or not line:
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def example_paragraphs(text):
    """The «Ejemplo (op):» paragraphs of a module, with their continuation lines."""
    paragraphs, current = [], None
    for line in text.split("\n"):
        if line.startswith("Ejemplo ("):
            current = [line]
            paragraphs.append(current)
        elif current is not None and line and not line.startswith(("«", "#", "Tras ", "Un ")):
            current.append(line)
        else:
            current = None
    return ["\n".join(p) for p in paragraphs]
