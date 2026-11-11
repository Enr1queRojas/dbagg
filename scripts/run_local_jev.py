"""Run the local WhatsApp pilot with Jev, GPT-4.1-mini extraction and mini fallback.

python scripts/run_local_jev.py --check validates local configuration only.
python scripts/run_local_jev.py starts the existing signed webhook on 127.0.0.1:8000.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn
from dotenv import load_dotenv

from dbagg.api.app import create_app
from dbagg.config import Settings
from dbagg.paths import project_root
from dbagg.database.catalog import load_catalog
from dbagg.routing.registry import available_routes


def prepare():
    """Select a dedicated pilot config in this process; never rewrite local files."""
    root = project_root()
    load_dotenv(root / ".env")
    config = Path(__file__).resolve().parents[1] / "config" / "llm.jev-local.toml"
    os.environ["LLM_CONFIG"] = str(config)
    os.environ["LLM_PROFILE"] = "openai"
    if not os.getenv("DBAGG_USAGE_PATH", "").strip():
        os.environ["DBAGG_USAGE_PATH"] = str(root / "work" / "whatsapp-jev" / "usage.jsonl")
    settings = Settings.from_env()
    modular = settings.llm.modular
    if not os.getenv(modular.jev_api_key_env, "").strip():
        raise ValueError(f"Falta {modular.jev_api_key_env} en el entorno o .env.")
    routes = available_routes(load_catalog(), settings.tables, modular.domains)
    if not routes:
        raise ValueError(
            "SQL_ALLOWED_TABLES no habilita rutas de clientes ni gastos para este piloto."
        )
    # Do not silently accept an edited config that would disable the tested cascade.
    if (
        not modular.enabled
        or modular.shadow
        or modular.router != "jev"
        or settings.llm.system_prompt
        or settings.llm.tool_descriptions
        or settings.llm.profile.provider != "openai"
        or settings.llm.profile.model != "gpt-4.1-mini"
        or modular.extraction_profile != "openai"
    ):
        raise ValueError(
            "El piloto requiere Jev, extracción GPT-4.1-mini, respaldo mini y módulos empaquetados; revisa llm.jev-local.toml."
        )
    extraction = settings.llm.profiles[modular.extraction_profile]
    return settings, {
        "mode": "local_whatsapp_jev_mini",
        "config": str(config),
        "router": modular.router,
        "routing_model": modular.jev_model,
        "jev_api_key_present": True,
        "shadow": modular.shadow,
        "min_confidence": modular.min_confidence,
        "interpretation_and_fallback_model": settings.llm.profile.model,
        "extraction_model": extraction.model,
        "extraction_api_key_present": True,
        "custom_prompts_loaded": False,
        "transcription_enabled": settings.llm.transcription.enabled,
        "available_routes": sorted(routes),
        "authorized_senders": len(settings.numbers),
        "authorized_sources": len(settings.tables),
        "usage_path": os.environ["DBAGG_USAGE_PATH"],
        "external_services_checked": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validar sin arrancar ni llamar APIs.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port debe estar entre 1 y 65535")
    try:
        settings, summary = prepare()
    except (ValueError, OSError) as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(dict(summary, local_url=f"http://127.0.0.1:{args.port}"), indent=2), flush=True
    )
    if args.check:
        return 0
    app = create_app(settings=settings)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
