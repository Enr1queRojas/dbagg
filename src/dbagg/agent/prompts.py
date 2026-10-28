"""Versioned intent interpretation; financial answers are rendered by business code."""

import json
from datetime import timedelta
from dbagg.business.calendar import business_today
from dbagg.business.customers import OPERATIONS
from dbagg.business.products import COMPARISONS, PRODUCT_OPERATIONS
from dbagg.agent.prompt_files import PROMPT_VERSION as PROMPT_VERSION, load_prompt_bundle


def reporting_calendar(today=None):
    today = today or business_today()
    monday = today - timedelta(days=today.weekday())
    return {
        "today": today.isoformat(),
        "week_start": monday.isoformat(),
        "week_end_exclusive": (today + timedelta(days=1)).isoformat(),
    }


def build_messages(schema, context, question, history=None, today=None, bundle=None):
    bundle = bundle or load_prompt_bundle()
    messages = [
        {
            "role": "system",
            "content": bundle.system
            + "\nObjetos autorizados: "
            + schema
            + "\nContexto de negocio: "
            + context
            + "\nCalendario de la zona horaria del negocio: "
            + json.dumps(reporting_calendar(today)),
        }
    ]
    messages.extend((dict(m) for m in history or [] if m.get("role") in ("user", "assistant")))
    messages.append({"role": "user", "content": question})
    return messages


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def tool_definitions(bundle=None):
    bundle = bundle or load_prompt_bundle()
    tools = [
        _tool(
            "consultar_negocio",
            bundle.descriptions["consultar_negocio"],
            {
                "operation": {"type": "string", "enum": list(OPERATIONS + PRODUCT_OPERATIONS)},
                "customer": {
                    "type": "string",
                    "description": "Nombre o código presente en la conversación. Omitir en ranking.",
                },
                "producto": {
                    "type": "string",
                    "description": "Solo existencias y ventas: palabra del producto (maíz, frijol). "
                    "Omitir si no piden un producto.",
                },
                "period": {
                    "type": "string",
                    "enum": [
                        "todo",
                        "esta_semana",
                        "semana_pasada",
                        "este_mes",
                        "mes_pasado",
                        "rango",
                    ],
                },
                "start": {
                    "type": "string",
                    "description": "Inicio inclusivo YYYY-MM-DD; solo en rango.",
                },
                "end": {
                    "type": "string",
                    "description": "Fin inclusivo YYYY-MM-DD; solo en rango.",
                },
                "comparar": {
                    "type": "string",
                    "enum": list(COMPARISONS),
                    "description": "Solo ventas: anio_anterior para comparar contra las mismas "
                    "fechas del año anterior; si no, periodo_anterior.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["operation"],
        ),
        _tool(
            "pedir_aclaracion",
            bundle.descriptions["pedir_aclaracion"],
            {
                "reason": {
                    "type": "string",
                    "enum": [
                        "cliente",
                        "periodo",
                        "criterio",
                        "escritura",
                        "fuera_alcance",
                        "comparacion",
                    ],
                }
            },
            ["reason"],
        ),
        _tool(
            "describir_tablas",
            bundle.descriptions["describir_tablas"],
            {
                "tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                }
            },
            ["tables"],
        ),
        _tool(
            "consultar_sql",
            bundle.descriptions["consultar_sql"],
            {"sql": {"type": "string"}},
            ["sql"],
        ),
    ]
    return tools
