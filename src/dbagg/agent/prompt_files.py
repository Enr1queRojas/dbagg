"""Load editable text without executing templates; fingerprint the actual instructions."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PROMPT_VERSION = "catalog-domains-v6"
TOOL_NAMES = frozenset(
    {"consultar_negocio", "pedir_aclaracion", "describir_tablas", "consultar_sql"}
)
TEMPLATES = Path(__file__).with_name("templates")


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
    system = _read(system_path, 24000)
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
