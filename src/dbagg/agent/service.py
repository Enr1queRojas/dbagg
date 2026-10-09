"""Bounded natural-language to SQL orchestration."""

import json
import logging
import re
import unicodedata
import pyodbc
import sqlglot
from sqlglot import exp
from openai import OpenAI
from dbagg.database.client import Database
from dbagg.database.validation import validate_sql
from dbagg.context.loader import load_business_context, context_version
from dbagg.agent.prompts import build_messages, tool_definitions
from dbagg.business.calendar import business_today
from dbagg.business.customers import CustomerQueries, PROTECTED_SOURCES, validate_context
from dbagg.business.evidence import BusinessError, render_exploratory

logger = logging.getLogger("uvicorn.error")


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
    def __init__(self, settings, db=None, client=None):
        self.settings = settings
        self.db = db if db is not None else Database(settings)
        self.client = (
            client
            if client is not None
            else OpenAI(api_key=settings.api_key, timeout=30, max_retries=1)
        )

    def answer(self, question, history=None):
        schema = json.dumps(sorted(self.settings.tables), ensure_ascii=False)
        logger.info("dbagg stage=catalogue_ready")
        context = load_business_context()
        self.last_trace = {"context_version": context_version(context)}
        today = business_today(getattr(self.settings, "business_timezone", "America/Mexico_City"))
        messages = build_messages(schema, context, question, history, today=today)
        tools = tool_definitions()
        attempts, descriptions, successful = 0, 0, 0
        described = set()
        latest_result = None
        latest_sources = []
        for _ in range(9):
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=0,
                max_tokens=1200,
                tools=tools,
                tool_choice="auto",
                parallel_tool_calls=False,
                messages=messages,
            )
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                logger.info(
                    "dbagg stage=%s", "answer_ready" if successful else "clarification_no_query"
                )
                if latest_result is not None:
                    self.last_trace.update(status="exploratory", sources=latest_sources)
                    try:
                        return render_exploratory(latest_result)
                    except BusinessError as exc:
                        self.last_trace.update(status="unavailable", sources=[])
                        return str(exc)
                self.last_trace.update(status="needs_clarification", sources=[])
                return "No obtuve resultados para respaldar una respuesta. ¿Qué cliente o reporte quieres consultar? Puedo consultar saldos, pagos y rankings; no modificar datos."
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in calls
                    ],
                }
            )
            for call in calls:
                try:
                    if call.function.name in {"consultar_negocio", "consultar_sql"}:
                        latest_result, latest_sources = None, []
                    arguments = json.loads(call.function.arguments)
                    if call.function.name == "consultar_negocio":
                        validate_context(context, arguments.get("operation"))
                        if arguments.get(
                            "operation"
                        ) != "ranking_deuda" and not customer_in_conversation(
                            arguments.get("customer"), question, history
                        ):
                            raise BusinessError(
                                "No pude vincular ese cliente con la conversación. Indica su nombre o código."
                            )
                        answer = CustomerQueries(
                            self.db, self.settings.tables, today=today
                        ).execute(**arguments)
                        self.last_trace.update(
                            status=answer.status,
                            operation=answer.operation,
                            sources=answer.sources,
                            evidence_id=answer.evidence_id,
                        )
                        logger.info("dbagg stage=business_answer status=%s", answer.status)
                        return answer.text
                    elif call.function.name == "pedir_aclaracion":
                        prompts = {
                            "cliente": "¿De qué cliente quieres consultar el saldo o los pagos? Indica su nombre o código.",
                            "periodo": "¿Qué período quieres consultar? Puedes indicar esta semana, este mes o un rango de fechas.",
                            "criterio": "Necesito precisar el concepto: ¿quieres saldo actual, pagos, último pago o un ranking de deuda?",
                            "escritura": "Solo puedo consultar información; no puedo modificar ni eliminar datos.",
                            "fuera_alcance": "Ese reporte todavía no tiene una definición de negocio verificada. Puedo ayudarte con saldos, pagos y rankings de deuda.",
                        }
                        self.last_trace.update(status="needs_clarification", sources=[])
                        return prompts[arguments["reason"]]
                    elif call.function.name == "describir_tablas":
                        descriptions += 1
                        selected = arguments["tables"]
                        if (
                            descriptions > 4
                            or not isinstance(selected, list)
                            or not 1 <= len(selected) <= 5
                        ):
                            raise ValueError("Límite de descripciones alcanzado.")
                        selected = [t.lower() for t in selected if isinstance(t, str)]
                        if not selected or not set(selected).issubset(self.settings.tables):
                            raise ValueError("Objetos no autorizados.")
                        logger.info("dbagg stage=schema_start")
                        result = json.loads(self.db.schema(selected))
                        described.update(selected)
                        logger.info("dbagg stage=schema_ok")
                    elif call.function.name == "consultar_sql":
                        attempts += 1
                        if attempts > 4:
                            raise ValueError("Límite de consultas alcanzado.")
                        sql = validate_sql(arguments["sql"], self.settings.tables)
                        referenced = {
                            f"{t.db}.{t.name}".lower()
                            for t in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)
                        }
                        if referenced & PROTECTED_SOURCES:
                            raise ValueError(
                                "Estas fuentes requieren las operaciones de negocio verificadas."
                            )
                        if not referenced.issubset(described):
                            raise ValueError(
                                "Primero inspecciona las columnas de los objetos usados."
                            )
                        logger.info("dbagg stage=sql_validated")
                        result = self.db.query(sql)
                        latest_result = result
                        latest_sources = sorted(referenced)
                        successful += 1
                        logger.info("dbagg stage=query_ok")
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
