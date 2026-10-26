"""Bounded natural-language to SQL orchestration."""

import json
import logging
import re
import unicodedata
import pyodbc
import sqlglot
from sqlglot import exp
from dbagg.database.client import Database
from dbagg.database.validation import validate_sql
from dbagg.context.loader import load_business_context, context_version
from dbagg.agent.prompts import build_messages, tool_definitions
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.providers import create_provider
from dbagg.agent.intent import requests_candidate_comparison, COMPARISON_CLARIFICATION
from dbagg.business.calendar import business_today
from dbagg.business.customers import CustomerQueries, PROTECTED_SOURCES, validate_context
from dbagg.business.evidence import BusinessError, render_exploratory

logger = logging.getLogger("uvicorn.error")

MAX_MODEL_ROUNDS = 9
MAX_SQL_QUERIES = 4
MAX_SCHEMA_REQUESTS = 4

CLARIFICATIONS = {
    "comparacion": COMPARISON_CLARIFICATION,
    "cliente": "¿De qué cliente quieres consultar el saldo o los pagos? Indica su nombre o código.",
    "periodo": "¿Qué período quieres consultar? Puedes indicar esta semana, este mes o un rango de fechas.",
    "criterio": "Necesito precisar el concepto: ¿quieres saldo actual, pagos, último pago o un ranking de deuda?",
    "escritura": "Solo puedo consultar información; no puedo modificar ni eliminar datos.",
    "fuera_alcance": "Ese reporte todavía no tiene una definición de negocio verificada. Puedo ayudarte con saldos, pagos y rankings de deuda.",
}


def customer_in_conversation(customer, question, history):
    def normalize(value):
        return "".join(
            c
            for c in unicodedata.normalize("NFKD", value.casefold())
            if not unicodedata.combining(c)
        )

    if not isinstance(customer, str) or not customer.strip():
        return False
    pattern = r"(?<!\w)" + re.escape(normalize(customer.strip())) + r"(?!\w)"
    texts = [question] + [
        m.get("content", "") for m in (history or []) if m.get("role") in {"user", "assistant"}
    ]
    return any(re.search(pattern, normalize(text)) for text in texts if isinstance(text, str))


class Assistant:
    def __init__(self, settings, db=None, client=None, provider=None):
        self.settings = settings
        self.db = db if db is not None else Database(settings)
        self.llm_config = getattr(settings, "llm", None) or LLMConfig(
            ModelProfile(model=settings.model, temperature=0)
        )
        self.provider = (
            provider
            if provider is not None
            else create_provider(
                self.llm_config.profile,
                getattr(settings, "api_key", ""),
                client=client,
            )
        )
        # Compatibility for tests/extensions that inject an OpenAI-compatible client.
        self.client = client

    def close(self):
        self.provider.close()

    def answer(self, question, history=None):
        schema = json.dumps(sorted(self.settings.tables), ensure_ascii=False)
        logger.info("dbagg stage=catalogue_ready")
        context = load_business_context()
        bundle = load_prompt_bundle(self.llm_config)
        profile = self.llm_config.profile
        self.last_trace = {
            "context_version": context_version(context),
            "prompt_version": bundle.version,
            "provider": profile.provider,
            "profile": profile.name,
            "model": profile.model,
        }
        if requests_candidate_comparison(question):
            self.last_trace.update(status="needs_clarification", sources=[])
            return COMPARISON_CLARIFICATION
        today = business_today(getattr(self.settings, "business_timezone", "America/Mexico_City"))
        messages = build_messages(schema, context, question, history, today=today, bundle=bundle)
        tools = tool_definitions(bundle)
        attempts, descriptions = 0, 0
        described = set()
        latest_result = None
        latest_sources = []
        for _ in range(MAX_MODEL_ROUNDS):
            message = self.provider.complete(messages, tools)
            calls = message.tool_calls
            if not calls:
                return self._finish_exploration(latest_result, latest_sources)
            messages.append(message.history_message)
            for call in calls:
                try:
                    if call.name in {"consultar_negocio", "consultar_sql"}:
                        latest_result, latest_sources = None, []
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Los argumentos deben ser un objeto JSON.")
                    if call.name == "consultar_negocio":
                        return self._consult_business(arguments, context, question, history, today)
                    elif call.name == "pedir_aclaracion":
                        text = CLARIFICATIONS[arguments["reason"]]
                        self.last_trace.update(status="needs_clarification", sources=[])
                        return text
                    elif call.name == "describir_tablas":
                        descriptions += 1
                        if descriptions > MAX_SCHEMA_REQUESTS:
                            raise ValueError("Límite de descripciones alcanzado.")
                        result = self._describe_tables(arguments["tables"], described)
                    elif call.name == "consultar_sql":
                        attempts += 1
                        if attempts > MAX_SQL_QUERIES:
                            raise ValueError("Límite de consultas alcanzado.")
                        result, latest_sources = self._query_sql(arguments["sql"], described)
                        latest_result = result
                    else:
                        raise ValueError("Herramienta no autorizada.")
                except BusinessError as exc:
                    self.last_trace.update(status="unavailable", sources=[])
                    return str(exc)
                except (ValueError, KeyError, TypeError, sqlglot.errors.ParseError):
                    logger.info("dbagg stage=sql_rejected")
                    result = {
                        "error": "Solicitud rechazada. Describe entre uno y cinco objetos autorizados "
                        "antes de consultar; usa solo SELECT y columnas del catálogo, sin CTE, "
                        "hints ni funciones no aprobadas. Para catálogo de clientes y cobranza usa consultar_negocio. "
                        "Máximo cuatro descripciones y cuatro SELECT."
                    }
                except pyodbc.ProgrammingError:
                    logger.info("dbagg stage=sql_programming_error")
                    result = {
                        "error": "SQL Server rechazó la consulta. Revisa columnas y sintaxis "
                        "con el catálogo; no inventes nombres. Si persiste pide aclaración."
                    }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        self.last_trace.update(status="needs_clarification", sources=[])
        return "No pude resolver la pregunta dentro del límite de consultas. ¿Puedes precisar el nombre o período?"

    def _consult_business(self, arguments, context, question, history, today):
        validate_context(context, arguments.get("operation"))
        if arguments.get("operation") != "ranking_deuda" and not customer_in_conversation(
            arguments.get("customer"), question, history
        ):
            raise BusinessError(
                "No pude vincular ese cliente con la conversación. Indica su nombre o código."
            )
        answer = CustomerQueries(self.db, self.settings.tables, today=today).execute(**arguments)
        self.last_trace.update(
            status=answer.status,
            operation=answer.operation,
            sources=answer.sources,
            evidence_id=answer.evidence_id,
        )
        logger.info("dbagg stage=business_answer status=%s", answer.status)
        return answer.text

    def _describe_tables(self, selected, described):
        if not isinstance(selected, list) or not 1 <= len(selected) <= 5:
            raise ValueError("Describe entre uno y cinco objetos.")
        if any(not isinstance(table, str) for table in selected):
            raise ValueError("Los nombres de objetos deben ser texto.")
        selected = [table.lower() for table in selected]
        if not set(selected).issubset(self.settings.tables):
            raise ValueError("Objetos no autorizados.")
        logger.info("dbagg stage=schema_start")
        result = json.loads(self.db.schema(selected))
        described.update(selected)
        logger.info("dbagg stage=schema_ok")
        return result

    def _query_sql(self, statement, described):
        sql = validate_sql(statement, self.settings.tables)
        referenced = {
            f"{table.db}.{table.name}".lower()
            for table in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)
        }
        if referenced & PROTECTED_SOURCES:
            raise ValueError("Estas fuentes requieren las operaciones de negocio verificadas.")
        if not referenced.issubset(described):
            raise ValueError("Primero inspecciona las columnas de los objetos usados.")
        logger.info("dbagg stage=sql_validated")
        result = self.db.query(sql)
        logger.info("dbagg stage=query_ok")
        return result, sorted(referenced)

    def _finish_exploration(self, result, sources):
        if result is None:
            logger.info("dbagg stage=clarification_no_query")
            self.last_trace.update(status="needs_clarification", sources=[])
            return "No obtuve resultados para respaldar una respuesta. ¿Qué cliente o reporte quieres consultar? Puedo consultar saldos, pagos y rankings; no modificar datos."
        logger.info("dbagg stage=answer_ready")
        self.last_trace.update(status="exploratory", sources=sources)
        try:
            return render_exploratory(result)
        except BusinessError as exc:
            self.last_trace.update(status="unavailable", sources=[])
            return str(exc)
