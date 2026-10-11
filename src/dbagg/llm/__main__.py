"""Local configuration helpers. These commands make no model or database calls."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from dbagg.agent.prompt_files import TEMPLATES, load_prompt_bundle
from dbagg.llm.config import load_llm_config
from dbagg.paths import project_root


def initialize(root):
    root = Path(root)
    config = root / "config"
    targets = [
        config / "llm.toml",
        config / "prompts" / "agent.md",
        config / "prompts" / "tools.json",
    ]
    if any(p.exists() for p in targets):
        raise ValueError("Ya existen archivos de configuración LLM; se conservaron sin cambios.")
    legacy_env = {"OPENAI_MODEL": os.environ["OPENAI_MODEL"]} if os.getenv("OPENAI_MODEL") else {}
    model = load_llm_config(root, environ=legacy_env).profile.model
    text = Path(__file__).with_name("default.toml").read_text(encoding="utf-8")
    text = text.replace('model = "gpt-4.1-mini"', "model = " + json.dumps(model), 1)
    text += '\n[prompts]\nsystem_file = "prompts/agent.md"\ntools_file = "prompts/tools.json"\n'
    contents = [
        text,
        (TEMPLATES / "system.md").read_text(encoding="utf-8"),
        (TEMPLATES / "tools.json").read_text(encoding="utf-8"),
    ]
    for path, content in zip(targets, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "check"])
    parser.add_argument("--profile", help="Validar un perfil sin cambiar la selección persistente.")
    args = parser.parse_args()
    root = project_root()
    load_dotenv(root / ".env")
    try:
        if args.command == "init":
            if args.profile:
                raise ValueError("--profile solo se usa con check.")
            initialize(root)
            print(
                "Creados config/llm.toml y config/prompts/{agent.md,tools.json}. No se copiaron credenciales."
            )
            return
        config = load_llm_config(root, selected_profile=args.profile)
        bundle = load_prompt_bundle(config)
        profile = config.profile
        print(
            json.dumps(
                {
                    "profile": profile.name,
                    "provider": profile.provider,
                    "model": profile.model,
                    "api_key_env": profile.api_key_env,
                    "api_key_present": bool(os.getenv(profile.api_key_env)),
                    "prompt_version": bundle.version,
                    "external_services_checked": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Configuración inválida: {exc}\n")


if __name__ == "__main__":
    main()
