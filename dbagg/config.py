"""Validated runtime configuration; never log secret values."""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from dbagg.paths import project_root
from dbagg.database.connection import parse_odbc_options

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str
    meta_token: str = field(repr=False)
    app_secret: str = field(repr=False)
    verify_token: str = field(repr=False)
    phone_id: str
    graph_version: str
    numbers: frozenset[str]
    tables: frozenset[str]
    connection: str = field(repr=False)
    feedback_enabled: bool = False
    feedback_path: Path | None = None

    @classmethod
    def from_env(cls):
        load_dotenv(project_root() / ".env")
        required = (
            "OPENAI_API_KEY",
            "META_ACCESS_TOKEN",
            "META_APP_SECRET",
            "META_VERIFY_TOKEN",
            "META_PHONE_NUMBER_ID",
            "META_GRAPH_VERSION",
            "WHATSAPP_ALLOWED_NUMBERS",
            "SQL_ALLOWED_TABLES",
            "DB_CONNECTION_STRING",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError("Faltan variables: " + ", ".join(missing))
        numbers = frozenset(x.strip() for x in os.environ["WHATSAPP_ALLOWED_NUMBERS"].split(","))
        tables = frozenset(x.strip().lower() for x in os.environ["SQL_ALLOWED_TABLES"].split(","))
        tables = frozenset(
            t
            for t in tables
            if t.split(".")[-1] not in {"login_access_data", "__efmigrationshistory"}
        )
        if not tables:
            raise ValueError("No hay objetos de negocio autorizados.")
        if any(not re.fullmatch(r"[0-9]{7,15}", n) for n in numbers):
            raise ValueError("WHATSAPP_ALLOWED_NUMBERS: usar dígitos internacionales sin +.")
        if any(not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", t) for t in tables):
            raise ValueError("SQL_ALLOWED_TABLES: usar schema.objeto, sin comodines.")
        version = os.environ["META_GRAPH_VERSION"]
        if not re.fullmatch(r"v[0-9]+\.0", version):
            raise ValueError("META_GRAPH_VERSION debe tener formato vNN.0.")
        if not os.environ["META_PHONE_NUMBER_ID"].isdigit():
            raise ValueError("META_PHONE_NUMBER_ID debe ser numérico.")
        connection = os.environ["DB_CONNECTION_STRING"]
        # Verified TLS by default; explicit temporary exception for the local demo.
        options = parse_odbc_options(connection)
        if options.get("encrypt", "").lower() not in ("yes", "mandatory", "strict"):
            raise ValueError("DB_CONNECTION_STRING requiere Encrypt=yes.")
        trust = options.get("trustservercertificate", "").lower()
        if trust not in ("no", "false"):
            if (
                trust not in ("yes", "true")
                or os.getenv("DB_ALLOW_UNVERIFIED_TLS", "").lower() != "true"
            ):
                raise ValueError(
                    "DB_CONNECTION_STRING requiere TrustServerCertificate=no. "
                    "La demo temporal requiere DB_ALLOW_UNVERIFIED_TLS=true explícito."
                )
            logger.warning(
                "dbagg: modo demo TLS sin verificación de identidad del servidor; "
                "el cifrado sigue siendo obligatorio."
            )
        return cls(
            os.environ["OPENAI_API_KEY"],
            os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            os.environ["META_ACCESS_TOKEN"],
            os.environ["META_APP_SECRET"],
            os.environ["META_VERIFY_TOKEN"],
            os.environ["META_PHONE_NUMBER_ID"],
            version,
            numbers,
            tables,
            connection,
            os.getenv("FEEDBACK_ENABLED", "").lower() == "true",
            project_root() / "data" / "feedback.sqlite3",
        )
