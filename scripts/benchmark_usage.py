"""Exercise complete conversations against a live model API and a synthetic local DB.

Run from a checkout: .venv/bin/python scripts/benchmark_usage.py --output work/live
--mock uses explicitly artificial provider counts for instrumentation verification only.
No WhatsApp messages or production database requests are sent.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from openai import OpenAI

from dbagg.agent.service import Assistant
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.services.conversation import ConversationService
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase
from tests.unit.test_llm_providers import call, openai_response

EXPENSES = "proadel.gastos_data"


class DemoDatabase(SyntheticDatabase):
    def __init__(self):
        super().__init__()
        self.customer("DEMO1", "Cliente Demo", 100)
        self.payment(1, "2026-11-01", 25, customer="DEMO1")
        self.conn.execute(f"CREATE TABLE {EXPENSES} (ID INTEGER, IMPORTE NUMERIC)")
        self.conn.execute(f"INSERT INTO {EXPENSES} VALUES (1, 42)")

    def schema(self, sources):
        entries = json.loads(super().schema(sources))
        if EXPENSES in sources:
            entries.append({"table": EXPENSES, "columns": [
                {"name": "ID", "type": "int"}, {"name": "IMPORTE", "type": "decimal"}
            ]})
        return json.dumps(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    mode = "mock" if args.mock else "live"
    manifest = {"mode": mode, "model": "gpt-4.1-mini", "database": "synthetic",
                "messages": [], "status": "running"}
    manifest_path = args.output / "benchmark.json"
    if not args.mock and not os.getenv("OPENAI_API_KEY"):
        manifest.update(status="blocked", reason="OPENAI_API_KEY missing")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("Prueba real bloqueada: falta OPENAI_API_KEY. No se hicieron llamadas.")
        return 2
    usage_path = args.output / "usage.jsonl"
    if usage_path.exists():
        raise SystemExit("Usa un directorio nuevo para no mezclar ejecuciones.")
    os.environ["DBAGG_USAGE_PATH"] = str(usage_path.resolve())
    profile = ModelProfile(temperature=0, max_retries=0)
    http = None
    client = None
    if args.mock:
        decisions = [
            ("pedir_aclaracion", {"reason": "criterio"}),
            ("consultar_negocio", {"operation": "saldo", "customer": "Cliente Demo"}),
            ("consultar_negocio", {"operation": "total_pagos", "customer": "Cliente Demo"}),
            ("describir_tablas", {"tables": [EXPENSES]}),
            ("consultar_sql", {"sql": f"SELECT SUM(IMPORTE) AS total FROM {EXPENSES}"}),
            (None, {}),
        ]

        def respond(request):
            name, arguments = decisions.pop(0)
            body = openai_response([call(name, json.dumps(arguments))] if name else None)
            # Fixed fixture numbers, never presented as measured production usage.
            body["usage"] = {"prompt_tokens": 1000, "completion_tokens": 50,
                             "total_tokens": 1050,
                             "prompt_tokens_details": {"cached_tokens": 200}}
            return httpx.Response(200, json=body)

        http = httpx.Client(transport=httpx.MockTransport(respond))
        client = OpenAI(api_key="synthetic", http_client=http, max_retries=0)
    settings = SimpleNamespace(
        model=profile.model, api_key=os.getenv("OPENAI_API_KEY", ""),
        tables={CUSTOMERS, PAYMENTS, EXPENSES}, llm=LLMConfig(profile),
        feedback_enabled=False, gap_log_enabled=False,
    )
    db = DemoDatabase()
    assistant = Assistant(settings, db=db, client=client)
    delivered = []
    conversation = ConversationService(settings, assistant, lambda sender, text: delivered.append(text))
    questions = ["Hola", "¿Cuál es el saldo del Cliente Demo?",
                 "¿Y cuánto ha pagado en total?", "¿Cuál es el total de gastos registrados?"]
    try:
        for index, question in enumerate(questions, 1):
            before = len(usage_path.read_text().splitlines()) if usage_path.exists() else 0
            conversation.process("synthetic-sender", question)
            events = [json.loads(line) for line in usage_path.read_text().splitlines()[before:]]
            calls = [e for e in events if e["event"] == "usage"]
            manifest["messages"].append({
                "id": index, "question": question, "answer": delivered[-1],
                "request_id": events[0]["request_id"], "calls": len(calls),
                "known_total_tokens": sum(c["total_tokens"] or 0 for c in calls),
                "missing_usage_calls": sum(c["total_tokens"] is None for c in calls),
                "error_calls": sum(c["status"] == "error" for c in calls),
            })
        manifest["status"] = (
            "completed_with_errors" if any(m["error_calls"] for m in manifest["messages"])
            else "completed"
        )
    finally:
        assistant.close()
        db.conn.close()
        if client:
            client.close()
        if http:
            http.close()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": mode, "status": manifest["status"], "messages": len(manifest["messages"])}))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
