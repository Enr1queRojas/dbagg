"""Exercise complete conversations against a live model API and a synthetic local DB.

Run from a checkout: .venv/bin/python scripts/benchmark_usage.py --output work/live
--mock uses explicitly artificial provider counts for instrumentation verification only.
No WhatsApp messages or production database requests are sent.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

# Keep tests/ fixtures importable, but load dbagg from this checkout's src layout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from dbagg.agent.service import Assistant
from dbagg.llm.config import LLMConfig, ModelProfile, ModularConfig
from dbagg.paths import project_root
from dbagg.routing.jev import JevRouter
from dbagg.services.conversation import ConversationService
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase
from tests.unit.test_llm_providers import call, openai_response

EXPENSES = "proadel.gastos_data"


class DemoDatabase(SyntheticDatabase):
    def __init__(self):
        super().__init__()
        self.customer("DEMO1", "Cliente Demo", 100)
        self.payment(1, "2026-11-01", 25, customer="DEMO1")
        self.conn.execute(f"CREATE TABLE {EXPENSES} (ID INTEGER, IMPORTE NUMERIC, ESTADO TEXT)")
        self.conn.execute(f"INSERT INTO {EXPENSES} VALUES (1, 42, 'ACTIVA')")
        self.conn.execute(f"INSERT INTO {EXPENSES} VALUES (2, 999, 'CANCELADA')")

    def schema(self, sources):
        entries = json.loads(super().schema(sources))
        if EXPENSES in sources:
            entries.append(
                {
                    "table": EXPENSES,
                    "columns": [
                        {"name": "ID", "type": "int"},
                        {"name": "IMPORTE", "type": "decimal"},
                        {"name": "ESTADO", "type": "nvarchar"},
                    ],
                }
            )
        return json.dumps(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--variant", choices=["legacy", "reference", "jev"], default="legacy")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Umbral solo experimental de Jev; no es calibración de producción.",
    )
    args = parser.parse_args()
    if not 0 <= args.min_confidence <= 1:
        parser.error("min-confidence debe estar entre 0 y 1")
    load_dotenv(project_root() / ".env")
    args.output.mkdir(parents=True, exist_ok=True)
    mode = "mock" if args.mock else "live"
    manifest = {
        "mode": mode,
        "model": "gpt-4.1-mini",
        "variant": args.variant,
        "min_confidence": args.min_confidence if args.variant == "jev" else None,
        "database": "synthetic",
        "messages": [],
        "status": "running",
    }
    manifest_path = args.output / "benchmark.json"
    usage_path = args.output / "usage.jsonl"
    if usage_path.exists() or manifest_path.exists():
        raise SystemExit("Usa un directorio nuevo para no mezclar ejecuciones.")
    required = ["OPENAI_API_KEY"] + (["TYPESAFE_API_KEY"] if args.variant == "jev" else [])
    missing = [name for name in required if not os.getenv(name)]
    if not args.mock and missing:
        manifest.update(status="blocked", reason="Missing: " + ", ".join(missing))
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("Prueba real bloqueada: faltan " + ", ".join(missing) + ". No se hicieron llamadas.")
        return 2
    os.environ["DBAGG_USAGE_PATH"] = str(usage_path.resolve())
    profile = ModelProfile(temperature=0, max_retries=0)
    http = None
    client = None
    jev_http = None
    if args.mock:
        decisions = [
            ("pedir_aclaracion", {"reason": "criterio"}),
            ("consultar_negocio", {"operation": "saldo", "customer": "Cliente Demo"}),
            ("consultar_negocio", {"operation": "total_pagos", "customer": "Cliente Demo"}),
            ("describir_tablas", {"tables": [EXPENSES]}),
            (
                "consultar_sql",
                {"sql": f"SELECT SUM(IMPORTE) AS total FROM {EXPENSES} WHERE ESTADO = 'ACTIVA'"},
            ),
            (None, {}),
        ]

        def respond(request):
            payload = json.loads(request.content)
            if args.variant == "legacy":
                name, arguments = decisions.pop(0)
            else:
                tools = {t["function"]["name"]: t["function"] for t in payload["tools"]}
                if "select_route" in tools:
                    question = json.loads(payload["messages"][-1]["content"])["question"]
                    route = (
                        "gastos.exploracion"
                        if "gastos" in question
                        else "clientes.total_pagos"
                        if "pagado" in question
                        else "clientes.saldo"
                    )
                    name, arguments = "select_route", {"route": route}
                elif "consultar_negocio" in tools:
                    operation = tools["consultar_negocio"]["parameters"]["properties"]["operation"][
                        "enum"
                    ][0]
                    name, arguments = (
                        "consultar_negocio",
                        {"operation": operation, "customer": "Cliente Demo"},
                    )
                elif any(
                    m.get("role") == "assistant"
                    and any(
                        c["function"]["name"] == "consultar_sql" for c in m.get("tool_calls", [])
                    )
                    for m in payload["messages"]
                ):
                    name, arguments = None, {}
                else:
                    name, arguments = (
                        "consultar_sql",
                        {
                            "sql": f"SELECT SUM(IMPORTE) AS total FROM {EXPENSES} WHERE ESTADO = 'ACTIVA'"
                        },
                    )
            body = openai_response([call(name, json.dumps(arguments))] if name else None)
            # Fixed fixture numbers, never presented as measured production usage.
            body["usage"] = {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "total_tokens": 1050,
                "prompt_tokens_details": {"cached_tokens": 200},
            }
            return httpx.Response(200, json=body)

        http = httpx.Client(transport=httpx.MockTransport(respond))
        client = OpenAI(api_key="synthetic", http_client=http, max_retries=0)
    settings = SimpleNamespace(
        model=profile.model,
        api_key=os.getenv("OPENAI_API_KEY", ""),
        tables={CUSTOMERS, PAYMENTS, EXPENSES},
        llm=LLMConfig(
            profile,
            modular=ModularConfig(
                enabled=args.variant != "legacy",
                router="jev" if args.variant == "jev" else "reference",
                min_confidence=args.min_confidence if args.variant == "jev" else None,
            ),
        ),
        feedback_enabled=False,
        gap_log_enabled=False,
    )
    db = DemoDatabase()
    assistant = Assistant(settings, db=db, client=client)
    if args.mock and args.variant == "jev":

        def respond_jev(request):
            payload = json.loads(request.content)
            question = payload["state"]["question"]
            route = (
                "gastos.exploracion"
                if "gastos" in question
                else "clientes.total_pagos"
                if "pagado" in question
                else "clientes.saldo"
            )
            options = payload["questions"]["route"]["criteria"]
            return httpx.Response(
                200,
                json={
                    "model": "jev-1.14.0",
                    "answers": {
                        "route": {
                            "type": "choice",
                            "choice": route,
                            "confidence": 1,
                            "probabilities": {name: float(name == route) for name in options},
                        }
                    },
                    "usage": {"input_tokens": 100, "output_tokens": 2},
                },
            )

        assistant.router.close()
        jev_http = httpx.Client(transport=httpx.MockTransport(respond_jev))
        assistant.router = JevRouter(settings.llm.modular, "synthetic", client=jev_http)
    delivered = []
    conversation = ConversationService(
        settings, assistant, lambda sender, text: delivered.append(text)
    )
    questions = [
        "Hola",
        "¿Cuál es el saldo del Cliente Demo?",
        "¿Y cuánto ha pagado en total?",
        "¿Cuál es el total de gastos registrados?",
    ]
    try:
        for index, question in enumerate(questions, 1):
            before = (
                len(usage_path.read_text(encoding="utf-8").splitlines())
                if usage_path.exists()
                else 0
            )
            conversation.process("synthetic-sender", question)
            events = [
                json.loads(line)
                for line in usage_path.read_text(encoding="utf-8").splitlines()[before:]
            ]
            calls = [e for e in events if e["event"] == "usage"]
            expected = {2: "100", 3: "25", 4: "42"}.get(index)
            answer_valid = expected is None or bool(
                re.search(r"(?<![\d.])" + expected + r"(?:\.00)?(?!\d|[.,]\d)", delivered[-1])
            )
            manifest["messages"].append(
                {
                    "id": index,
                    "question": question,
                    "answer": delivered[-1],
                    "request_id": events[0]["request_id"],
                    "calls": len(calls),
                    "answer_valid": answer_valid,
                    "fallbacks": [
                        e
                        for e in assistant.last_trace.get("events", [])
                        if e.get("event") == "fallback"
                    ],
                    "known_total_tokens": sum(c["total_tokens"] or 0 for c in calls),
                    "missing_usage_calls": sum(c["total_tokens"] is None for c in calls),
                    "error_calls": sum(c["status"] == "error" for c in calls),
                    "error_stages": sum(
                        e["event"] == "stage" and e["status"] == "error" for e in events
                    ),
                }
            )
        manifest["status"] = (
            "completed_with_errors"
            if any(
                m["error_calls"] or m["error_stages"] or not m["answer_valid"]
                for m in manifest["messages"]
            )
            else "completed"
        )
    finally:
        assistant.close()
        db.conn.close()
        if client:
            client.close()
        if http:
            http.close()
        if jev_http:
            jev_http.close()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        json.dumps(
            {"mode": mode, "status": manifest["status"], "messages": len(manifest["messages"])}
        )
    )
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
