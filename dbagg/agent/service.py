"""Bounded natural-language to SQL orchestration."""

import json
import logging
import pyodbc
import sqlglot
from sqlglot import exp
from openai import OpenAI
from dbagg.database.client import Database
from dbagg.database.validation import validate_sql
from dbagg.context.loader import load_business_context, context_version
from dbagg.agent.prompts import build_messages, tool_definitions

logger = logging.getLogger("uvicorn.error")


class Assistant:
    def __init__(self, settings):
        self.settings = settings
        self.db = Database(settings)
        self.client = OpenAI(api_key=settings.api_key, timeout=30, max_retries=1)

    def answer(self, question, history=None):
        schema = json.dumps(sorted(self.settings.tables), ensure_ascii=False)
        logger.info("dbagg stage=catalogue_ready")
        context = load_business_context()
        self.last_trace = {"context_version": context_version(context)}
        messages = build_messages(schema, context, question, history)
        tools = tool_definitions()
        attempts, descriptions, successful = 0, 0, 0
        described = set()
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
                return (message.content or "¿Qué información necesitas consultar?")[:3500]
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
                    arguments = json.loads(call.function.arguments)
                    if call.function.name == "describir_tablas":
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
                        if not referenced.issubset(described):
                            raise ValueError(
                                "Primero inspecciona las columnas de los objetos usados."
                            )
                        logger.info("dbagg stage=sql_validated")
                        result = self.db.query(sql)
                        successful += 1
                        logger.info("dbagg stage=query_ok")
                    else:
                        raise ValueError("Herramienta no autorizada.")
                except (ValueError, KeyError, TypeError, sqlglot.errors.ParseError):
                    logger.info("dbagg stage=sql_rejected")
                    result = {
                        "error": "Solicitud rechazada. Describe entre uno y cinco objetos autorizados "
                        "antes de consultar; usa solo SELECT y columnas del catálogo, sin CTE, "
                        "hints ni funciones no aprobadas. Máximo cuatro descripciones y cuatro SELECT."
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
        return "No pude resolver la pregunta dentro del límite de consultas. ¿Puedes precisar el nombre o período?"
