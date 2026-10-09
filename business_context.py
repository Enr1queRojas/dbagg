"""Load reviewed business guidance without overwriting local definitions."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAX_FILE_CHARS = 16000
MAX_CONTEXT_CHARS = 32000


def _read_object(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError(f'{path.name} debe contener un objeto JSON.')
    if len(json.dumps(data, ensure_ascii=False)) > MAX_FILE_CHARS:
        raise ValueError(f'{path.name} supera el límite de contexto de {MAX_FILE_CHARS} caracteres.')
    return data


def _merge(base, local):
    result = dict(base)
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_business_context(root=None):
    root = Path(root) if root is not None else ROOT
    default = root / 'business_context.default.json'
    local = root / 'business_context.json'
    data = _read_object(default) if default.exists() else {}
    if local.exists():
        data = _merge(data, _read_object(local))
    rendered = json.dumps(data, ensure_ascii=False)
    if len(rendered) > MAX_CONTEXT_CHARS:
        raise ValueError('Contexto de negocio demasiado grande; simplifica las definiciones.')
    return rendered


if __name__ == '__main__':
    payload = load_business_context()
    print(f'Contexto de negocio válido ({len(payload)} caracteres).')
