"""Strict model profiles. Configuration contains secret names, never secret values."""

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dbagg.paths import project_root

PROVIDERS = {"openai", "glm", "gemini", "anthropic", "openai_compatible"}
EXTRA_OPTIONS = {"thinking", "reasoning_effort", "google", "output_config", "verbosity"}


@dataclass(frozen=True)
class ModelProfile:
    name: str = "openai"
    provider: str = "openai"
    model: str = "gpt-4.1-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    max_output_tokens: int = 1200
    temperature: float | None = None
    timeout_seconds: float = 30
    max_retries: int = 1
    token_parameter: str = "max_tokens"
    extra_body: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TranscriptionConfig:
    """Voice-note transcription through an OpenAI-compatible /audio/transcriptions API."""

    enabled: bool = True
    model: str = "gpt-4o-mini-transcribe"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    language: str = "es"
    max_seconds: int = 60
    timeout_seconds: float = 30


@dataclass(frozen=True)
class LLMConfig:
    profile: ModelProfile
    system_prompt: Path | None = None
    tool_descriptions: Path | None = None
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)


def _number(value, minimum, maximum, name, integer=False):
    valid_type = type(value) is int if integer else type(value) in {int, float}
    if not valid_type or not minimum <= value <= maximum:
        raise ValueError(f"LLM: {name} fuera del rango permitido.")
    return value


def _https_url(value):
    try:
        url = urlsplit(value)
        _ = url.port
        valid = (
            url.scheme == "https"
            and url.hostname
            and not (url.username or url.password or url.query or url.fragment)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("LLM: base_url requiere HTTPS, sin credenciales, query ni fragmento.")


def _transcription(data):
    if data is None:
        return TranscriptionConfig()
    if not isinstance(data, dict) or set(data) - set(TranscriptionConfig.__dataclass_fields__):
        raise ValueError("LLM: sección transcription con campos no admitidos.")
    config = TranscriptionConfig(**data)
    if type(config.enabled) is not bool:
        raise ValueError("LLM: transcription.enabled debe ser true o false.")
    if not isinstance(config.model, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}", config.model
    ):
        raise ValueError("LLM: modelo de transcripción inválido.")
    if not isinstance(config.api_key_env, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,99}", config.api_key_env
    ):
        raise ValueError("LLM: transcription.api_key_env debe ser el nombre de una variable.")
    if not isinstance(config.language, str) or not re.fullmatch(r"[a-z]{2}", config.language):
        raise ValueError("LLM: transcription.language debe ser un código ISO de dos letras.")
    _https_url(config.base_url)
    _number(config.max_seconds, 5, 300, "transcription.max_seconds", integer=True)
    _number(config.timeout_seconds, 1, 120, "transcription.timeout_seconds")
    return config


def _profile(name, data):
    allowed = set(ModelProfile.__dataclass_fields__) - {"name"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(
            "LLM: perfil con campos no admitidos; las claves se indican por api_key_env."
        )
    if not {"provider", "model", "api_key_env", "base_url"}.issubset(data):
        raise ValueError("LLM: el perfil requiere provider, model, api_key_env y base_url.")
    profile = ModelProfile(name=name, **data)
    if not isinstance(profile.provider, str) or profile.provider not in PROVIDERS:
        raise ValueError("LLM: proveedor no soportado.")
    if not isinstance(profile.model, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}", profile.model
    ):
        raise ValueError("LLM: identificador de modelo inválido.")
    if not isinstance(profile.api_key_env, str) or not re.fullmatch(
        r"[A-Z][A-Z0-9_]{0,99}", profile.api_key_env
    ):
        raise ValueError("LLM: api_key_env debe ser el nombre de una variable de entorno.")
    _https_url(profile.base_url)
    _number(profile.max_output_tokens, 128, 16384, "max_output_tokens", integer=True)
    _number(profile.timeout_seconds, 1, 120, "timeout_seconds")
    _number(profile.max_retries, 0, 2, "max_retries", integer=True)
    if profile.temperature is not None:
        _number(profile.temperature, 0, 1 if profile.provider == "anthropic" else 2, "temperature")
    if not isinstance(profile.token_parameter, str) or profile.token_parameter not in {
        "max_tokens",
        "max_completion_tokens",
    }:
        raise ValueError("LLM: token_parameter debe ser max_tokens o max_completion_tokens.")
    if profile.provider == "anthropic" and profile.token_parameter != "max_tokens":
        raise ValueError("LLM: Anthropic usa max_tokens.")
    if not isinstance(profile.extra_body, dict) or set(profile.extra_body) - EXTRA_OPTIONS:
        raise ValueError(
            "LLM: extra_body solo admite opciones de razonamiento; no puede redefinir herramientas ni mensajes."
        )
    try:
        extra = json.dumps(profile.extra_body, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError("LLM: extra_body requiere valores JSON válidos.") from None
    if len(extra) > 4000:
        raise ValueError("LLM: opciones demasiado grandes.")
    return profile


def load_llm_config(root=None, environ=None, selected_profile=None):
    env = os.environ if environ is None else environ
    root = Path(root) if root is not None else project_root()
    explicit_path = env.get("LLM_CONFIG")
    path = Path(explicit_path) if explicit_path else root / "config" / "llm.toml"
    if not path.is_absolute() and explicit_path:
        path = root / path
    local = path.is_file()
    if explicit_path and not local:
        raise ValueError("LLM_CONFIG no apunta a un archivo existente.")
    if not local:
        path = Path(__file__).with_name("default.toml")
    if path.stat().st_size > 64000:
        raise ValueError("LLM: configuración demasiado grande.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeError):
        raise ValueError("LLM: archivo TOML inválido.") from None
    if (
        set(data) - {"version", "active_profile", "profiles", "prompts", "transcription"}
        or type(data.get("version")) is not int
        or data["version"] != 1
    ):
        raise ValueError("LLM: formato de configuración no soportado.")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("LLM: faltan perfiles.")
    # Validate every profile's structure, but require only the active secret at startup.
    parsed = {}
    for name, values in profiles.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("LLM: nombre de perfil inválido.")
        parsed[name] = _profile(name, values)
    name = selected_profile or env.get("LLM_PROFILE") or data.get("active_profile")
    if not isinstance(name, str) or name not in parsed:
        raise ValueError("LLM: el perfil seleccionado no existe.")
    profile = parsed[name]
    if not local and name == "openai" and env.get("OPENAI_MODEL"):
        values = dict(profiles[name], model=env["OPENAI_MODEL"])
        profile = _profile(name, values)
    prompts = data.get("prompts", {})
    if not isinstance(prompts, dict) or set(prompts) - {"system_file", "tools_file"}:
        raise ValueError("LLM: configuración de prompts inválida.")

    def prompt_path(key):
        if key not in prompts:
            return None
        value = prompts[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("LLM: ruta de prompt inválida.")
        candidate = Path(value)
        return candidate if candidate.is_absolute() else path.parent / candidate

    return LLMConfig(
        profile,
        prompt_path("system_file"),
        prompt_path("tools_file"),
        _transcription(data.get("transcription")),
    )
