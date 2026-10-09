"""Load packaged guidance and optional local business overrides."""

import hashlib
import json
from pathlib import Path

from dbagg.paths import project_root

MAX_FILE_CHARS = 16000
MAX_CONTEXT_CHARS = 32000


def _read_object(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} debe contener un objeto JSON.")
    if len(json.dumps(data, ensure_ascii=False)) > MAX_FILE_CHARS:
        raise ValueError(
            f"{path.name} supera el límite de contexto de {MAX_FILE_CHARS} caracteres."
        )
    return data


def merge_context(base, local):
    result = dict(base)
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_context(result[key], value)
        else:
            result[key] = value
    return result


def load_business_context(root=None):
    if root is None:
        default = Path(__file__).with_name("default.json")
        root = project_root()
    else:
        root = Path(root)
        default = root / "business_context.default.json"
    # Legacy root overrides remain supported. config/ takes precedence if both exist.
    data = _read_object(default) if default.exists() else {}
    for local in (root / "business_context.json", root / "config" / "business_context.json"):
        if local.exists():
            data = merge_context(data, _read_object(local))
    rendered = json.dumps(data, ensure_ascii=False)
    if len(rendered) > MAX_CONTEXT_CHARS:
        raise ValueError("Contexto de negocio demasiado grande; simplifica las definiciones.")
    return rendered


def context_version(context):
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]


def main():
    payload = load_business_context()
    print(
        f"Contexto de negocio válido ({len(payload)} caracteres, versión {context_version(payload)})."
    )


if __name__ == "__main__":
    main()
