"""Bounded natural-language to SQL orchestration."""

import json
import logging
import re
import unicodedata
import pyodbc
import sqlglot
from sqlglot import exp
from dbagg.database.catalog import load_catalog
from dbagg.database.client import Database
from dbagg.database.validation import validate_sql
from dbagg.context.loader import load_business_context, context_version
from dbagg.agent.prompts import build_messages, tool_definitions
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.providers import create_provider
from dbagg.agent.entity import (
    conversation_entity,
    explicit_entity,
    listed_row,
    mixed_list_choice,
)
from dbagg.agent.intent import (
    COMPARISON_CLARIFICATION,
    NOT_A_CUSTOMER_HINT,
    PERIOD_HINT,
    RANKING_HINT,
    list_position,
    listed_codes,
    names_month_and_year,
    requests_candidate_comparison,
    requests_ranking,
)
from dbagg.business.calendar import business_today
from dbagg.business.customers import (
    CUSTOMERS,
    NOT_FOUND_ANSWERS,
    PROTECTED_SOURCES,
    CustomerQueries,
    validate_context,
)
from dbagg.business.evidence import BusinessAnswer, BusinessError, label, render_exploratory
from dbagg.business.parties import CUSTOMER, SUPPLIER, PartyDirectory, ambiguous_answer
from dbagg.business.products import PRODUCT_OPERATIONS, ProductQueries
from dbagg.business.suppliers import (
    SUPPLIER_MATCHES_NOT_FOUND,
    SUPPLIER_NOT_FOUND,
    SUPPLIER_ONLY_OPERATIONS,
    SUPPLIER_PROTECTED_SOURCES,
    SUPPLIERS,
    SupplierQueries,
    validate_supplier_context,
)

logger = logging.getLogger("uvicorn.error")

ALL_NOT_FOUND_ANSWERS = NOT_FOUND_ANSWERS | {SUPPLIER_NOT_FOUND, SUPPLIER_MATCHES_NOT_FOUND}
MAX_MODEL_ROUNDS = 9
MAX_SQL_QUERIES = 4
MAX_SCHEMA_REQUESTS = 4


class ToolHint(Exception):
    """Rejected tool choice that the model may correct in the next round."""


CLARIFICATIONS = {
    "comparacion": COMPARISON_CLARIFICATION,
    "cliente": "¿De qué cliente o proveedor quieres consultar el saldo o los pagos? Indica su nombre o código.",
    "periodo": "¿Qué período quieres consultar? Puedes indicar esta semana, este mes o un rango de fechas.",
    "criterio": "Necesito precisar el concepto. De clientes y proveedores: saldo actual, saldos de varios con un nombre, pagos, último pago o ranking de deuda. De proveedores, también última nota y movimientos. También puedo consultar existencias de inventario, gastos registrados y compras a proveedores. ¿Qué quieres revisar?",
    "escritura": "Solo puedo consultar información; no puedo modificar ni eliminar datos.",
    "fuera_alcance": "Ese reporte todavía no tiene una definición de negocio verificada. Puedo ayudarte con saldos, pagos y rankings de deuda, y consultar existencias de inventario, gastos y compras.",
}

NO_EVIDENCE = (
    "No obtuve resultados para respaldar una respuesta. ¿Qué cliente o reporte quieres "
    "consultar? Puedo consultar saldos, pagos y rankings; no modificar datos."
)


def _normalize(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)
    )


def customer_in_conversation(customer, question, history):
    if not isinstance(customer, str) or not customer.strip():
        return False
    pattern = r"(?<!\w)" + re.escape(_normalize(customer.strip())) + r"(?!\w)"
    texts = [question] + [
        m.get("content", "") for m in (history or []) if m.get("role") in {"user", "assistant"}
    ]
    return any(re.search(pattern, _normalize(text)) for text in texts if isinstance(text, str))


def code_confirmed(customer, question, history):
    """A value is a code only if the user called it one or the service already showed it as one;
    a model's flag alone must not turn a loose surname into a single customer."""
    if not isinstance(customer, str) or not customer.strip():
        return False
    pattern = r"(?<!\w)codigo\s+" + re.escape(_normalize(customer.strip())) + r"(?!\w)"
    texts = [question] + [
        m.get("content", "") for m in (history or []) if m.get("role") == "assistant"
    ]
    return any(re.search(pattern, _normalize(text)) for text in texts if isinstance(text, str))


def _open_list(history):
    """Codes still awaiting a choice; asking who they meant does not close the list."""
    for message in reversed(history or []):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, str):
            continue
        if content not in (CLARIFICATIONS["cliente"], NO_EVIDENCE):
            codes = listed_codes(content)
            return codes if len(codes) >= 2 else []
    return []


def customer_left_unselected(customer, question, history):
    """A list of customers identifies none: the customer must be named or pointed at, because
    the model tends to continue with the first row and would answer about the wrong person."""
    codes = _open_list(history)
    position = list_position(question)
    return (
        bool(codes)
        and not customer_in_conversation(customer, question, None)
        and (position is None or not 1 <= position <= len(codes))
    )


def chosen_customer(customer, question, history):
    """«El segundo» is resolved from the list itself; the model may pick another row."""
    codes = _open_list(history)
    position = list_position(question)
    if not position or not 1 <= position <= len(codes):
        return None
    return None if customer_in_conversation(customer, question, None) else codes[position - 1]


def _abandons_result(call):
    """After a successful exploratory query, a generic clarification or a jump to the
    customer operations discards a valid answer; specific clarifications still apply."""
    if call.name == "consultar_negocio":
        return True
    if call.name != "pedir_aclaracion":
        return False
    try:
        arguments = json.loads(call.arguments)
    except (TypeError, ValueError):
        return False
    return isinstance(arguments, dict) and arguments.get("reason") == "criterio"


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
        self.catalog = load_catalog()

    @property
    def tables(self):
        """Configured objects, never beyond the source catalogue."""
        return self.catalog.restrict(self.settings.tables)

    def close(self):
        self.provider.close()

    def answer(self, question, history=None):
        schema = json.dumps(self.catalog.cards(self.tables), ensure_ascii=False)
        self._customer_missed = False
        logger.info("dbagg stage=catalogue_ready")
        context = load_business_context()
        bundle = load_prompt_bundle(self.llm_config)
        profile = self.llm_config.profile
        self.last_trace = {
            "context_version": context_version(context),
            "prompt_version": bundle.version,
            "catalog_version": self.catalog.version,
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
        period_hinted = False
        for round_number in range(MAX_MODEL_ROUNDS):
            message = self.provider.complete(messages, tools)
            calls = message.tool_calls
            if not calls and round_number == 0:
                # Long conversations can make a model reply in prose; its text never reaches the
                # user, so ask once for the decision. Providers may not support forcing a tool.
                logger.info("dbagg stage=model_skipped_tools")
                try:
                    message = self.provider.complete(messages, tools, require_tool=True)
                    calls = message.tool_calls
                except Exception as exc:
                    logger.info("dbagg stage=require_tool_failed error_type=%s", type(exc).__name__)
            if not calls:
                return self._finish_exploration(latest_result, latest_sources)
            messages.append(message.history_message)
            for call in calls:
                if latest_result is not None and _abandons_result(call):
                    # Some models keep calling tools after a valid answer; deliver it instead.
                    logger.info("dbagg stage=exploration_kept")
                    return self._finish_exploration(latest_result, latest_sources)
                try:
                    if call.name in {"consultar_negocio", "consultar_sql"}:
                        latest_result, latest_sources = None, []
                    arguments = json.loads(call.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Los argumentos deben ser un objeto JSON.")
                    if call.name == "consultar_negocio":
                        return self._consult_business(arguments, context, question, history, today)
                    elif call.name == "pedir_aclaracion":
                        if (
                            arguments["reason"] == "periodo"
                            and not period_hinted
                            and names_month_and_year(question)
                        ):
                            period_hinted = True
                            raise ToolHint(PERIOD_HINT)
                        text = CLARIFICATIONS[arguments["reason"]]
                        self.last_trace.update(
                            status="needs_clarification",
                            sources=[],
                            clarification=arguments["reason"],
                        )
                        if arguments["reason"] == "fuera_alcance":
                            self.last_trace["gap"] = "fuera_alcance"
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
                except ToolHint as exc:
                    result = {"error": str(exc)}
                except BusinessError as exc:
                    self.last_trace.update(status="unavailable", sources=[])
                    return str(exc)
                except (ValueError, KeyError, TypeError, sqlglot.errors.ParseError) as exc:
                    # Validator messages are fixed texts; parser errors may echo SQL, so omit them.
                    reason = (
                        str(exc)[:400]
                        if type(exc) is ValueError
                        else "SQL no reconocido por el validador."
                        if isinstance(exc, sqlglot.errors.ParseError)
                        else "Argumentos inválidos."
                    )
                    logger.info(
                        "dbagg stage=sql_rejected kind=%s reason=%s",
                        type(exc).__name__,
                        reason[:80] if type(exc) is ValueError else "-",
                    )
                    result = {
                        "error": "Solicitud rechazada. Motivo: " + reason + " Reglas: describe "
                        "entre uno y cinco objetos autorizados antes de consultar; usa solo SELECT "
                        "de un objeto con columnas explícitas de su descripción, sin SELECT *, "
                        "JOIN no declarado, CTE ni hints. Para catálogo de clientes y cobranza usa "
                        "consultar_negocio. Máximo cuatro descripciones y cuatro SELECT. Corrige "
                        "según el motivo; no repitas la misma consulta."
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
        self.last_trace.update(status="needs_clarification", sources=[], gap="limite_consultas")
        return "No pude resolver la pregunta dentro del límite de consultas. ¿Puedes precisar el nombre o período?"

    def _consult_business(self, arguments, context, question, history, today):
        model_entity = arguments.get("entidad")
        arguments = {k: v for k, v in arguments.items() if k != "entidad"}
        if arguments.get("operation") in PRODUCT_OPERATIONS:
            answer = ProductQueries(self.db, self.tables, today=today).execute(**arguments)
            return self._business_answer(answer)
        directory = PartyDirectory(self.db, self.tables)
        routed = directory.supports_suppliers
        if not routed:
            validate_context(context, arguments.get("operation"))
        if arguments.get("operation") == "ranking_deuda" and not requests_ranking(question):
            logger.info("dbagg stage=ranking_not_requested")
            raise ToolHint(RANKING_HINT)
        single_customer = arguments.get("operation") not in (
            "ranking_deuda",
            "saldos_coincidencias",
            "contar_coincidencias",
        )
        chosen = (
            chosen_customer(arguments.get("customer"), question, history)
            if single_customer
            else None
        )
        picked = mixed_list_choice(question, history) if routed and single_customer else None
        if chosen:
            arguments = {**arguments, "customer": chosen, "codigo_exacto": True}
        elif picked and picked[1]:
            arguments = {**arguments, "customer": picked[1], "codigo_exacto": True}
        if arguments.get("operation") != "ranking_deuda" and not customer_in_conversation(
            arguments.get("customer"), question, history
        ):
            raise BusinessError(
                "No pude vincular ese cliente con la conversación. Indica su nombre o código."
            )
        if (
            single_customer
            and not picked
            and customer_left_unselected(arguments.get("customer"), question, history)
        ):
            logger.info("dbagg stage=customer_not_selected")
            self.last_trace.update(
                status="needs_clarification", sources=[], clarification="cliente"
            )
            return CLARIFICATIONS["cliente"]
        customer_arguments = {
            k: v for k, v in arguments.items() if k not in ("producto", "comparar")
        }
        customer_arguments["codigo_exacto"] = arguments.get("codigo_exacto") is True and bool(
            chosen
            or (picked and picked[1])
            or code_confirmed(arguments.get("customer"), question, history)
        )
        entity = CUSTOMER
        if routed:
            if chosen:
                entity = listed_row(history, list_position(question))[0]
            elif picked:
                entity = picked[0]
            else:
                entity = self._party_entity(
                    directory, customer_arguments, model_entity, question, history
                )
            if isinstance(entity, BusinessAnswer):
                return self._business_answer(entity)
        if entity == SUPPLIER:
            validate_supplier_context(context)
            supplier_arguments = dict(customer_arguments)
            supplier_arguments["supplier"] = supplier_arguments.pop("customer", None)
            answer = SupplierQueries(self.db, self.tables, today=today).execute(
                **supplier_arguments
            )
        elif arguments.get("operation") in SUPPLIER_ONLY_OPERATIONS:
            answer = BusinessAnswer.make(
                "Ese reporte todavía solo está disponible para proveedores.",
                "needs_clarification",
                arguments["operation"],
                [],
            )
        else:
            if routed:
                validate_context(context, arguments.get("operation"))
            answer = CustomerQueries(self.db, self.tables, today=today).execute(
                **customer_arguments
            )
        if answer.text in ALL_NOT_FOUND_ANSWERS and not self._customer_missed:
            # One retry: the name may be an expense section, product or warehouse.
            self._customer_missed = True
            logger.info("dbagg stage=customer_not_found_retry")
            raise ToolHint(NOT_A_CUSTOMER_HINT)
        return self._business_answer(answer)

    def _party_entity(self, directory, arguments, model_entity, question, history):
        """Which catalogue a question means, or the answer that asks; see docs/business-queries.md."""
        explicit = explicit_entity(question)
        model = model_entity if model_entity in (CUSTOMER, SUPPLIER) else None
        term = arguments.get("customer")
        if not isinstance(term, str) or not term.strip():
            return explicit or model or CUSTOMER
        term = term.strip()
        if not explicit:
            remembered = conversation_entity(term, history)
            if remembered:
                return remembered
        found = directory.find(term, exact_code=arguments.get("codigo_exacto") is True)
        sources = [CUSTOMERS, SUPPLIERS]
        operation = arguments.get("operation")
        if explicit:
            other = CUSTOMER if explicit == SUPPLIER else SUPPLIER
            if not found[explicit] and found[other]:
                return BusinessAnswer.make(
                    f"No encontré a {label(term)} como {explicit}. "
                    f"{label(term)} sí aparece como {other}.",
                    "needs_clarification",
                    operation,
                    sources,
                )
            return explicit
        if found[CUSTOMER] and found[SUPPLIER]:
            if model:
                return model
            logger.info("dbagg stage=party_ambiguous")
            return BusinessAnswer.make(
                ambiguous_answer(term, found), "needs_clarification", operation, sources
            )
        if found[SUPPLIER]:
            return SUPPLIER
        return CUSTOMER if found[CUSTOMER] else model or CUSTOMER

    def _business_answer(self, answer):
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
        if not set(selected).issubset(self.tables):
            raise ValueError("Objetos no autorizados.")
        logger.info("dbagg stage=schema_start")
        result = self.catalog.filter_schema(json.loads(self.db.schema(selected)))
        described.update(selected)
        logger.info("dbagg stage=schema_ok")
        return result

    def _query_sql(self, statement, described):
        sql = validate_sql(statement, self.tables, self.catalog)
        referenced = {
            f"{table.db}.{table.name}".lower()
            for table in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)
        }
        if referenced & (PROTECTED_SOURCES | SUPPLIER_PROTECTED_SOURCES):
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
            self.last_trace.update(status="needs_clarification", sources=[], gap="sin_evidencia")
            return NO_EVIDENCE
        logger.info("dbagg stage=answer_ready")
        # Answered from unverified SQL: a candidate for a new verified operation.
        self.last_trace.update(status="exploratory", sources=sources, gap="exploratoria")
        try:
            return render_exploratory(result)
        except BusinessError as exc:
            self.last_trace.update(status="unavailable", sources=[])
            return str(exc)
