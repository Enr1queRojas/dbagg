"""Bounded natural-language to SQL orchestration."""

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import replace
from datetime import timedelta
import pyodbc
import sqlglot
from sqlglot import exp
from dbagg.database.catalog import load_catalog
from dbagg.database.client import Database
from dbagg.database.validation import validate_sql
from dbagg.context.loader import load_business_context, context_version
from dbagg.agent.prompts import (
    build_messages,
    build_stable_messages,
    reporting_calendar,
    tool_definitions,
)
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.agent.context_builder import WRONG_ROUTE_REASON, select_context, validate_tool_call
from dbagg.routing.registry import available_routes, domain_routes
from dbagg.routing.service import ReferenceRouter
from dbagg.routing.jev import JevRouter
from dbagg.routing.state import ConversationState
from dbagg.routing.policy import (
    mentions_account_words,
    needs_expense_year,
    pre_route,
    route_is_consistent,
)
from dbagg.llm.types import ModelProtocolError, ModelReply, ToolCall
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.providers import create_provider
from dbagg.llm.usage import stage, task_scope
from dbagg.limits import MAX_ROWS
from dbagg.reporting.bank_pdf import bank_report_filename, bank_report_pdf
from dbagg.reporting.statement_pdf import statement_filename, statement_pdf
from dbagg.agent.entity import (
    conversation_entity,
    dictated_code,
    explicit_entity,
    listed_row,
    mixed_list_choice,
    named_row,
    names_entity,
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
from dbagg.business.banks import (
    BANK_OPERATIONS,
    BANK_PROTECTED_SOURCES,
    BANKS,
    REPORT_DATES_QUESTION,
    BankQueries,
    validate_bank_context,
)
from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.customers import (
    CUSTOMERS,
    NOT_FOUND_ANSWERS,
    PROTECTED_SOURCES,
    CustomerQueries,
    validate_context,
)
from dbagg.business.evidence import (
    BusinessAnswer,
    BusinessError,
    amount,
    label,
    records,
    render_exploratory,
)
from dbagg.business.parties import CUSTOMER, SUPPLIER, PartyDirectory, ambiguous_answer
from dbagg.business.products import PRODUCT_OPERATIONS, ProductQueries
from dbagg.business.statements import (
    STATEMENT_OPERATION,
    DocumentAttachment,
    StatementQueries,
)
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
BANK_NAMES_TTL = 600


class ToolHint(Exception):
    """Rejected tool choice that the model may correct in the next round."""


CLARIFICATIONS = {
    "comparacion": COMPARISON_CLARIFICATION,
    "cliente": "¿De qué cliente quieres consultar el saldo o los pagos? Indica su nombre o código.",
    "periodo": "¿Qué período quieres consultar? Puedes indicar esta semana, este mes o un rango de fechas.",
    "criterio": "Necesito precisar el concepto. De clientes: saldo actual, saldos de varios clientes con un nombre, pagos, último pago, última nota o ranking de deuda. También puedo consultar existencias de inventario, gastos registrados y compras a proveedores. ¿Qué quieres revisar?",
    "escritura": "Solo puedo consultar información; no puedo modificar ni eliminar datos.",
    "fuera_alcance": "Ese reporte todavía no tiene una definición de negocio verificada. Puedo ayudarte con saldos, pagos y rankings de deuda, y consultar existencias de inventario, gastos y compras.",
}

# Only offered where the supplier sources are authorised.
SUPPLIER_CLARIFICATIONS = {
    "cliente": "¿De qué cliente o proveedor quieres consultar el saldo o los pagos? Indica su nombre o código.",
    "criterio": "Necesito precisar el concepto. De clientes y proveedores: saldo actual, saldos de varios con un nombre, pagos, último pago, última nota o ranking de deuda. De proveedores, también movimientos. También puedo consultar existencias de inventario, gastos registrados y compras a proveedores. ¿Qué quieres revisar?",
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
        if content not in (
            CLARIFICATIONS["cliente"],
            SUPPLIER_CLARIFICATIONS["cliente"],
            NO_EVIDENCE,
        ):
            codes = listed_codes(content)
            return codes if len(codes) >= 2 else []
    return []


def customer_left_unselected(customer, question, history):
    """A list of customers identifies none: the customer must be named or pointed at, because
    the model tends to continue with the first row and would answer about the wrong person."""
    codes = _open_list(history)
    position = list_position(question, codes)
    return (
        bool(codes)
        and not customer_in_conversation(customer, question, None)
        and (position is None or not 1 <= position <= len(codes))
    )


def chosen_customer(customer, question, history):
    """«El segundo» is resolved from the list itself; the model may pick another row."""
    codes = _open_list(history)
    position = list_position(question, codes)
    if not position or not 1 <= position <= len(codes):
        return None
    return None if customer_in_conversation(customer, question, None) else codes[position - 1]


BANK_REPORT_WORDS = re.compile(
    r"\b(reporte|movimientos?|depositos?|retiros?|estado de cuenta|entradas|salidas)\b"
)


def bank_in_conversation(question, history):
    """The account of the latest answer when the question continues with its report.

    «Dame un reporte de este mes» after «Cuenta X» names no account, and a model tends to ask
    which report; the service already knows. The latest answer decides, so a change of topic
    in between ends the link.
    """
    for message in reversed(history or []):
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, str):
            continue
        found = re.match(r"Cuenta (\S.*?)(?: \((?:[^()]*)\))?$", content.splitlines()[0])
        if not found:
            return None
        asked_dates = REPORT_DATES_QUESTION in content
        if asked_dates or BANK_REPORT_WORDS.search(_normalize(question)):
            return found.group(1)
        return None
    return None


def bank_report_followup(question, history):
    """Resolve only short, explicit relative-period continuations of a known bank."""
    bank = bank_in_conversation(question, history)
    if not bank:
        return None
    match = re.fullmatch(
        r"[\s¿¡]*(?:(?:dame|quiero|muestrame|ahora)\s+)?"
        r"(?:(?:un|el)\s+)?(?:reporte\s+)?"
        r"(?:(?:de|del|para)\s+)?(este mes|mes pasado)[\s?!.{},]*",
        _normalize(question),
    )
    if not match:
        return None
    return {
        "operation": "movimientos_banco",
        "banco": bank,
        "period": "este_mes" if match[1] == "este mes" else "mes_pasado",
    }


PERIOD_OPERATIONS = frozenset({"pagos", "total_pagos", "estado_cuenta", "movimientos"})
RELATIVE_PERIODS = {
    "este mes": "este_mes",
    "mes pasado": "mes_pasado",
    "el mes pasado": "mes_pasado",
    "esta semana": "esta_semana",
    "semana pasada": "semana_pasada",
    "la semana pasada": "semana_pasada",
}


def pending_clarification_answer(question, history, state):
    """Arguments that answer the clarification the service itself asked, or None.

    Only a listed code or a valid position after «¿cuál?», or exactly a relative period after
    «¿qué período?». Anything else is a new question for the normal flow. The arguments still
    pass every check of ``_consult_business``; this only skips the model.
    """
    if not isinstance(state, ConversationState) or not state.pending_operation:
        return None
    if not (state.previous_route or "").startswith("clientes."):
        return None
    operation = state.pending_operation
    if state.pending_clarification == "cliente":
        codes = _open_list(history)
        if not codes:
            return None
        text = _normalize(question).strip(" ¿?¡!.,")
        position = list_position(question, codes)
        named = [code for code in codes if _normalize(code) == text]
        if len(named) == 1:
            code = named[0]
        elif position and 1 <= position <= len(codes):
            code = codes[position - 1]
        else:
            return None
        arguments = {"operation": operation, "customer": code, "codigo_exacto": True}
        if operation in PERIOD_OPERATIONS and state.period and state.period != "todo":
            arguments["period"] = state.period
            if state.period == "rango":
                arguments.update(start=state.start, end=state.end)
        return arguments
    if state.pending_clarification == "periodo":
        period = RELATIVE_PERIODS.get(_normalize(question).strip(" ¿?¡!.,"))
        code = state.selected_customer_id
        if not period or operation not in PERIOD_OPERATIONS or not code:
            return None
        if not code_confirmed(code, "", history):
            return None
        arguments = {"operation": operation, "customer": code, "codigo_exacto": True}
        if state.entity in {"cliente", "proveedor"}:
            arguments["entidad"] = state.entity
        return {**arguments, "period": period}
    return None


DOMAIN_ROUTE_FLOOR = 0.05
MAX_DOMAIN_ROUTES = 3


def tiered_decision(decision, routes, config):
    """(level, candidate routes) from Jev's probabilities.

    1. confidence ≥ min_confidence and margin ≥ min_margin → that operation;
    2. else a domain whose routes add up to ≥ min_domain_confidence → up to three of its
       routes above a small floor, most probable first;
    3. else legacy.
    """
    probabilities = {k: v for k, v in (decision.probabilities or {}).items() if k in routes}
    ranked = sorted((decision.probabilities or {}).values(), reverse=True)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0) if ranked else 0.0
    top = routes.get(decision.route)
    if (
        top is not None
        and decision.confidence is not None
        and config.min_confidence is not None
        and decision.confidence >= config.min_confidence
        and margin >= (config.min_margin or 0.0)
    ):
        return "operation", [top]
    if config.min_domain_confidence is not None and probabilities:
        sums = {}
        for name, probability in probabilities.items():
            sums[routes[name].domain] = sums.get(routes[name].domain, 0.0) + probability
        domain = max(sums, key=sums.get)
        if sums[domain] >= config.min_domain_confidence:
            chosen = sorted(
                (
                    name
                    for name, probability in probabilities.items()
                    if routes[name].domain == domain and probability > DOMAIN_ROUTE_FLOOR
                ),
                key=lambda name: -probabilities[name],
            )[:MAX_DOMAIN_ROUTES]
            if chosen:
                return "domain", [routes[name] for name in chosen]
    return "legacy", []


def _rejected_call_messages(reply, reason):
    """The rejected assistant turn plus one tool result per call, carrying the reason."""
    calls = list(reply.tool_calls)
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }
    error = json.dumps(
        {"error": "Solicitud rechazada. Motivo: " + reason + " Corrige según el motivo."},
        ensure_ascii=False,
    )
    return [assistant] + [
        {"role": "tool", "tool_call_id": call.id, "content": error} for call in calls
    ]


def _reason(call):
    try:
        arguments = json.loads(call.arguments)
    except (TypeError, ValueError):
        return None
    return arguments.get("reason") if isinstance(arguments, dict) else None


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
        # Each background task answers and delivers on its own thread.
        self._local = threading.local()
        self.extraction_provider = None
        modular = self.llm_config.modular
        if modular.enabled and modular.extraction_profile:
            extraction = self.llm_config.profiles[modular.extraction_profile]
            key = os.getenv(extraction.api_key_env)
            if extraction == self.llm_config.profile:
                # The active profile already interprets: share its instance, no second client.
                self.extraction_provider = self.provider
            elif key:
                self.extraction_provider = create_provider(extraction, key)
        self.router = None
        if modular.enabled and modular.router == "jev":
            self.router = JevRouter(modular, os.getenv(modular.jev_api_key_env, ""))

    @property
    def last_trace(self):
        return getattr(self._local, "trace", {})

    @last_trace.setter
    def last_trace(self, value):
        self._local.trace = value

    @property
    def _customer_missed(self):
        return getattr(self._local, "customer_missed", False)

    @_customer_missed.setter
    def _customer_missed(self, value):
        self._local.customer_missed = value

    @property
    def next_state(self):
        return getattr(self._local, "next_state", ConversationState())

    @property
    def tables(self):
        """Configured objects, never beyond the source catalogue."""
        return self.catalog.restrict(self.settings.tables)

    def close(self):
        self.provider.close()
        if self.extraction_provider is not None and self.extraction_provider is not self.provider:
            self.extraction_provider.close()
        if self.router is not None:
            self.router.close()

    def _complete(self, messages, tools, require_tool=False, provider=None, task="interpretation"):
        provider = self.provider if provider is None else provider
        started = time.monotonic()
        event = {"event": "usage", "task": task, "status": "error", "usage": None}
        profile = getattr(provider, "profile", None)
        if isinstance(profile, ModelProfile):
            event.update(provider=profile.provider, model=profile.model)
        try:
            with task_scope(task):
                reply = provider.complete(messages, tools, require_tool=require_tool)
            event.update(status="ok", usage=reply.usage)
            return reply
        finally:
            event["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            self.last_trace.setdefault("events", []).append(event)

    def _route_operation(self):
        """The operation of this turn's modular route, if any."""
        route = self.last_trace.get("route") or ""
        return route.split(".", 1)[1] if route.startswith("clientes.") else None

    def _bank_names(self):
        """Account codes and names, cached for ten minutes; empty when banks are not granted."""
        if BANKS not in self.tables:
            return ()
        cached = getattr(self, "_bank_names_cache", None)
        if cached and time.monotonic() - cached[0] < BANK_NAMES_TTL:
            return cached[1]
        try:
            result = self.db.query(
                f"SELECT TOP {MAX_ROWS} [CODIGO] AS codigo, [CUENTA BANCARIA] AS cuenta "
                f"FROM {BANKS} ORDER BY [CODIGO]",
                row_limit=MAX_ROWS,
            )
            names = tuple(
                value
                for row in records(result)
                for value in (row.get("codigo"), row.get("cuenta"))
                if isinstance(value, str) and value.strip()
            )
        except Exception as exc:
            logger.info("dbagg stage=bank_names_failed error_type=%s", type(exc).__name__)
            return ()
        self._bank_names_cache = (time.monotonic(), names)
        return names

    def _select_modular(self, question, history, context, bundle):
        config = self.llm_config.modular
        if not config.enabled:
            return None
        # Custom prompts are complete local policies. Do not override them with packaged modules.
        if self.llm_config.system_prompt or self.llm_config.tool_descriptions:
            self.last_trace["modular_fallback"] = "custom_prompts"
            return None
        routes = available_routes(self.catalog, self.tables, config.domains)
        if not routes:
            return None
        names = self._bank_names() if mentions_account_words(question) else ()
        if pre_route(question, history, self.next_state, names) == "legacy":
            # The same veto as route_is_consistent, applied before paying for a classification.
            self.last_trace["local_decision"] = "pre_route_legacy"
            self.last_trace["modular_fallback"] = "route_inconsistent"
            logger.info("dbagg stage=pre_route_legacy")
            return None

        assistant = self

        class ObservedReference:
            def complete(self, messages, tools, require_tool=False):
                return assistant._complete(messages, tools, require_tool, task="routing")

        reference = ReferenceRouter(ObservedReference())
        started = time.monotonic()
        decision = None
        reason = None
        try:
            decision = (self.router or reference).decide(question, history, routes, self.next_state)
        except Exception as exc:
            reason = type(exc).__name__
        elapsed = round((time.monotonic() - started) * 1000, 3)
        if self.router is not None:
            self.last_trace.setdefault("events", []).append(
                {
                    "event": "usage",
                    "task": "routing",
                    "provider": "jev",
                    "model": decision.model if decision else config.jev_model,
                    "status": "ok" if decision else "error",
                    "usage": decision.usage if decision else None,
                    "duration_ms": elapsed,
                }
            )
        if decision is not None:
            logger.info(
                "dbagg stage=routing_selected provider=%s route=%s confidence=%s shadow=%s",
                decision.provider,
                decision.route,
                decision.confidence,
                config.shadow,
            )
            self.last_trace.setdefault("events", []).append(
                {
                    "event": "routing",
                    "provider": decision.provider,
                    "route": decision.route,
                    "confidence": decision.confidence,
                    "probabilities": decision.probabilities,
                    "duration_ms": elapsed,
                    "shadow": config.shadow,
                }
            )
        if config.shadow:
            if reason:
                self.last_trace["modular_fallback"] = reason
            return None
        tiered = config.min_margin is not None or config.min_domain_confidence is not None
        candidates = None
        if self.router is not None and decision is not None and tiered:
            level, candidates = tiered_decision(decision, routes, config)
            self.last_trace["routing_level"] = level
            self.last_trace.setdefault("events", []).append(
                {"event": "routing_level", "level": level, "candidates": len(candidates)}
            )
            if level == "legacy":
                # Jev answered: an unsure distribution goes to the full interpreter without
                # paying for a second classification. The mini classifier covers only errors.
                self.last_trace["modular_fallback"] = "low_confidence"
                return None
        elif (
            self.router is not None
            and decision is not None
            and (
                decision.confidence is None
                or config.min_confidence is None
                or decision.confidence < config.min_confidence
            )
        ):
            reason = "low_confidence"
        if reason:
            self.last_trace.setdefault("events", []).append(
                {
                    "event": "fallback",
                    "task": "routing",
                    "reason": reason,
                }
            )
            if self.router is None:
                return None
            try:
                decision = reference.decide(question, history, routes, self.next_state)
                self.last_trace.setdefault("events", []).append(
                    {
                        "event": "routing",
                        "provider": decision.provider,
                        "route": decision.route,
                        "confidence": decision.confidence,
                        "probabilities": decision.probabilities,
                        "fallback": True,
                    }
                )
            except Exception:
                self.last_trace["modular_fallback"] = "routing_unavailable"
                return None
        if candidates is None:
            route = routes.get(decision.route)
            if route is None:
                self.last_trace["modular_fallback"] = "legacy_route"
                return None
            candidates = [route]
        consistent = [r for r in candidates if route_is_consistent(question, r)]
        if not consistent:
            self.last_trace["modular_fallback"] = "route_inconsistent"
            self.last_trace.setdefault("events", []).append(
                {
                    "event": "fallback",
                    "task": "routing",
                    "reason": "route_inconsistent",
                    "rejected_route": candidates[0].name,
                    "destination": "legacy",
                }
            )
            return None
        route = consistent[0]
        selected = select_context(
            consistent if len(consistent) > 1 else route,
            context,
            bundle,
            self.catalog,
            self.tables,
            domain_routes(routes, route.domain) if config.stable_prefix else None,
        )
        self._local.next_state = self.next_state.for_route(route.name)
        self._local.route_tables = selected.tables
        self.last_trace.update(route=route.name, prompt_version=selected.bundle.version)
        if len(consistent) > 1:
            self.last_trace["route_candidates"] = [r.name for r in consistent]
        self.last_trace.setdefault("events", []).append(
            {
                "event": "context_selection",
                "modules": list(selected.modules),
                "version": selected.bundle.version,
                "system_chars": len(selected.bundle.system),
                "business_chars": len(selected.context),
                "schema_chars": len(selected.schema),
                "tools_chars": len(json.dumps(selected.tools, ensure_ascii=False)),
            }
        )
        return selected

    @stage("answer")
    def answer(self, question, history=None, conversation_state=None):
        self._local.route_tables = None
        self._local.pending_request = None
        self._local.legacy_state = None
        self._local.next_state = (
            conversation_state
            if isinstance(conversation_state, ConversationState)
            else ConversationState()
        )
        schema = json.dumps(self.catalog.cards(self.tables), ensure_ascii=False)
        self._customer_missed = False
        self._local.document = None
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
        if self.llm_config.modular.enabled and re.fullmatch(
            r"[\s¡!¿?.,]*(hola|buenos dias|buenas tardes|buenas noches|gracias)[\s¡!¿?.,]*",
            _normalize(question),
        ):
            self.last_trace.update(status="local", sources=[])
            return (
                "Hola. Puedes consultar saldos, pagos, gastos o inventario. ¿Qué quieres revisar?"
            )
        if requests_candidate_comparison(question):
            self.last_trace.update(
                status="needs_clarification", sources=[], clarification="comparacion"
            )
            return COMPARISON_CLARIFICATION
        today = business_today(getattr(self.settings, "business_timezone", "America/Mexico_City"))
        followup = bank_report_followup(question, history) if BANKS in self.tables else None
        if followup is not None:
            self._local.next_state = ConversationState()
            self.last_trace["local_decision"] = "bank_report_followup"
            logger.info("dbagg stage=bank_report_followup_resolved")
            return self._consult_business(followup, context, question, history, today)
        pending = (
            pending_clarification_answer(question, history, self.next_state)
            if self.llm_config.modular.enabled
            else None
        )
        if pending is not None:
            self.last_trace["local_decision"] = "pending_clarification"
            if self.next_state.previous_route:
                self.last_trace["route"] = self.next_state.previous_route
            logger.info("dbagg stage=pending_clarification_resolved")
            try:
                return self._consult_business(pending, context, question, history, today)
            except ToolHint:
                # Not resolvable without the model after all; the normal flow decides.
                self.last_trace.pop("local_decision", None)
                self.last_trace.pop("route", None)
            except BusinessError as exc:
                self.last_trace.update(status="unavailable", sources=[])
                return str(exc)
        selected = self._select_modular(question, history, context, bundle)
        if selected is None:
            # Legacy starts clean; only a result the service verifies restores continuity.
            if self.llm_config.modular.enabled:
                self._local.legacy_state = self.next_state
            self._local.next_state = ConversationState()
        model_context = context
        full_bundle, full_schema = bundle, schema
        if selected is not None:
            bundle, model_context, schema = selected.bundle, selected.context, selected.schema
        state_text = "Estado conversacional confirmado (datos): " + json.dumps(
            self.next_state.payload(), ensure_ascii=False
        )
        if selected is not None and selected.suffix:
            messages = build_stable_messages(
                selected.bundle.system,
                schema,
                model_context,
                selected.suffix,
                "Calendario de la zona horaria del negocio: "
                + json.dumps(reporting_calendar(today))
                + "\n"
                + state_text,
                history,
                question,
            )
        else:
            messages = build_messages(
                schema, model_context, question, history, today=today, bundle=bundle
            )
            if selected is not None:
                messages[0]["content"] += "\n" + state_text
        tools = tool_definitions(
            bundle, suppliers=SUPPLIERS in self.tables, banks=BANKS in self.tables
        )
        if selected is not None:
            tools = selected.tools
            turn_routes = self.last_trace.get("route_candidates") or [self.last_trace.get("route")]
            needs_dates = any(
                route
                in {
                    "gastos.exploracion",
                    "clientes.pagos",
                    "clientes.total_pagos",
                    "clientes.estado_cuenta",
                }
                for route in turn_routes
            )
            if needs_dates and needs_expense_year(question, history, self.next_state):
                self.last_trace.update(
                    status="needs_clarification", sources=[], clarification="periodo"
                )
                self._local.next_state = replace(
                    self.next_state,
                    pending_clarification="periodo",
                    pending_operation=self._route_operation(),
                )
                return self._clarifications()["periodo"]
        attempts, descriptions = 0, 0
        described = set()
        latest_result = None
        latest_sources = []
        period_hinted = False
        bank_hinted = False
        extraction = (
            self.extraction_provider
            if "consultar_negocio" in {t["function"]["name"] for t in tools}
            else None
        )
        interpretation_calls = 0
        bounded_extraction = selected is not None and any(
            t["function"]["name"] == "consultar_negocio" for t in tools
        )
        wrong_route = False
        local_expense_source = (
            selected is not None
            and self.last_trace.get("route") == "gastos.exploracion"
            and "proadel.gastos_data" in selected.tables
            and re.search(r"\b(gastamos|gastos|gastado)\b", _normalize(question))
            and not re.search(r"\bsaldo\b", _normalize(question))
        )
        for round_number in range(MAX_MODEL_ROUNDS):
            round_tools = [
                t
                for t in tools
                if not (
                    selected is not None
                    and not described
                    and t["function"]["name"] == "consultar_sql"
                )
            ]
            if bounded_extraction and interpretation_calls >= 2:
                break
            interpretation_calls += 1
            if local_expense_source and round_number == 0:
                arguments = json.dumps(
                    {"tables": [self.catalog.sources["proadel.gastos_data"].display]}
                )
                call = ToolCall("local_expense_schema", "describir_tablas", arguments)
                message = ModelReply(
                    None,
                    (call,),
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": arguments},
                            }
                        ],
                    },
                )
            elif extraction is not None:
                # Full-context extraction may choose a business operation or request
                # schema, but SQL exploration remains on the reference provider.
                extraction_tools = [
                    t for t in round_tools if t["function"]["name"] != "consultar_sql"
                ]
                rejected = None
                try:
                    rejected = message = self._complete(
                        messages, extraction_tools, True, extraction, "extraction"
                    )
                    if len(message.tool_calls) != 1:
                        raise ModelProtocolError("Extracción inválida.")
                    validate_tool_call(message.tool_calls[0], extraction_tools)
                    self.last_trace.setdefault("events", []).append(
                        {
                            "event": "extraction",
                            "validation": "ok",
                        }
                    )
                except Exception as exc:
                    self.last_trace.setdefault("events", []).append(
                        {
                            "event": "fallback",
                            "task": "extraction",
                            "reason": type(exc).__name__,
                        }
                    )
                    interpretation_calls += 1
                    if rejected is not None and rejected.tool_calls and isinstance(exc, ValueError):
                        # The fallback sees what was rejected and why, as ToolHint does; the
                        # validator texts are fixed, never echoes of the model or the user.
                        messages.extend(_rejected_call_messages(rejected, str(exc)[:400]))
                    message = self._complete(
                        messages, round_tools, True, task="extraction_fallback"
                    )
                # Never retry extraction; corrective rounds use the reference profile.
                extraction = None
            else:
                message = self._complete(messages, round_tools)
            calls = message.tool_calls
            if (
                not calls
                and round_number == 0
                and (not bounded_extraction or interpretation_calls < 2)
            ):
                # Long conversations can make a model reply in prose; its text never reaches the
                # user, so ask once for the decision. Providers may not support forcing a tool.
                logger.info("dbagg stage=model_skipped_tools")
                try:
                    interpretation_calls += 1
                    message = self._complete(messages, round_tools, require_tool=True)
                    calls = message.tool_calls
                except Exception as exc:
                    logger.info("dbagg stage=require_tool_failed error_type=%s", type(exc).__name__)
            if not calls:
                return self._finish_exploration(latest_result, latest_sources)
            if selected is not None and len(calls) != 1:
                self.last_trace.update(
                    status="needs_clarification", sources=[], clarification="criterio"
                )
                return self._clarifications()["criterio"]
            if (
                selected is not None
                and not wrong_route
                and len(calls) == 1
                and calls[0].name == "pedir_aclaracion"
                and _reason(calls[0]) == WRONG_ROUTE_REASON
            ):
                # The modular route cannot express the request: one return to the full
                # interpreter in the same turn. Logged in the trace, never shown to the user.
                wrong_route = True
                logger.info("dbagg stage=modular_wrong_route")
                self.last_trace["modular_fallback"] = "wrong_route"
                self.last_trace.setdefault("events", []).append(
                    {
                        "event": "fallback",
                        "task": "interpretation",
                        "reason": "wrong_route",
                        "rejected_route": self.last_trace.pop("route", None),
                        "destination": "legacy",
                    }
                )
                self.last_trace.pop("route_candidates", None)
                self._local.legacy_state = self.next_state
                self._local.next_state = ConversationState()
                self._local.route_tables = None
                selected, bundle, schema = None, full_bundle, full_schema
                messages = build_messages(
                    schema, context, question, history, today=today, bundle=bundle
                )
                tools = tool_definitions(
                    bundle, suppliers=SUPPLIERS in self.tables, banks=BANKS in self.tables
                )
                extraction, bounded_extraction, local_expense_source = None, False, False
                continue
            if any(
                c.name == "pedir_aclaracion" and _reason(c) == WRONG_ROUTE_REASON for c in calls
            ):
                # Never a loop: a second «wrong route» (or one outside modular) asks for criteria.
                self.last_trace.update(
                    status="needs_clarification", sources=[], clarification="criterio"
                )
                return self._clarifications()["criterio"]
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
                    if selected is not None:
                        arguments = validate_tool_call(call, tools)
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
                        bank = (
                            bank_in_conversation(question, history)
                            if BANKS in self.tables and not bank_hinted
                            else None
                        )
                        if bank and arguments["reason"] in {"criterio", "periodo", "cliente"}:
                            bank_hinted = True
                            logger.info("dbagg stage=bank_report_hinted")
                            raise ToolHint(
                                f"La conversación sigue con la cuenta de banco {bank}. Usa "
                                'consultar_negocio(operation="movimientos_banco", '
                                f'banco="{bank}") con el período de la pregunta; sin fechas '
                                "omite period y el servicio las pide."
                            )
                        text = self._clarifications()[arguments["reason"]]
                        self.last_trace.update(
                            status="needs_clarification",
                            sources=[],
                            clarification=arguments["reason"],
                        )
                        self._local.next_state = replace(
                            self.next_state,
                            pending_clarification=arguments["reason"],
                            pending_operation=self._route_operation(),
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

    @stage("business")
    def _consult_business(self, arguments, context, question, history, today):
        model_entity = arguments.get("entidad")
        arguments = {k: v for k, v in arguments.items() if k != "entidad"}
        if arguments.get("operation") in BANK_OPERATIONS:
            return self._bank(arguments, context, today)
        bank = bank_in_conversation(question, history) if BANKS in self.tables else None
        if (
            bank
            and arguments.get("operation") == STATEMENT_OPERATION
            and not customer_in_conversation(arguments.get("customer"), question, None)
        ):
            # «Dame un reporte de este mes» right after «Cuenta X» is that account's report.
            logger.info("dbagg stage=bank_report_continued")
            return self._bank(
                {**arguments, "operation": "movimientos_banco", "banco": bank}, context, today
            )
        arguments = {k: v for k, v in arguments.items() if k != "banco"}
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
        if not routed and explicit_entity(question, arguments.get("customer")) == SUPPLIER:
            logger.info("dbagg stage=suppliers_not_enabled")
            return self._business_answer(
                BusinessAnswer.make(
                    "La consulta de proveedores no está habilitada en este servicio.",
                    "unavailable",
                    arguments.get("operation"),
                    [],
                )
            )
        named = (
            named_row(question, history)
            if single_customer and not chosen and _open_list(history)
            else None
        )
        row = None
        if named:
            # The person named a row; the model may have sent its code instead of the name.
            chosen = named[1]
            row = named if routed else None
        elif routed and chosen:
            row = listed_row(
                history,
                list_position(question, _open_list(history)),
                explicit_entity(question, arguments.get("customer")),
            )
            if row is None:
                logger.info("dbagg stage=customer_not_selected")
                self.last_trace.update(
                    status="needs_clarification", sources=[], clarification="cliente"
                )
                return self._clarifications()["cliente"]
            chosen = row[1]
        picked = (
            mixed_list_choice(question, history, arguments.get("customer"))
            if routed and single_customer and not chosen
            else None
        )
        if chosen:
            arguments = {**arguments, "customer": chosen, "codigo_exacto": True}
        elif picked:
            arguments = {
                **arguments,
                "customer": picked[1] or picked[2],
                "codigo_exacto": bool(picked[1]),
            }
        if arguments.get("operation") != "ranking_deuda" and not customer_in_conversation(
            arguments.get("customer"), question, history
        ):
            raise BusinessError(
                "No pude vincular ese cliente con la conversación. Indica su nombre o código."
            )
        if (
            single_customer
            and not picked
            and not named
            and customer_left_unselected(arguments.get("customer"), question, history)
        ):
            logger.info("dbagg stage=customer_not_selected")
            self.last_trace.update(
                status="needs_clarification", sources=[], clarification="cliente"
            )
            return self._clarifications()["cliente"]
        customer_arguments = {
            k: v for k, v in arguments.items() if k not in ("producto", "comparar")
        }
        customer_arguments["codigo_exacto"] = arguments.get("codigo_exacto") is True and bool(
            chosen
            or (picked and picked[1])
            or code_confirmed(arguments.get("customer"), question, history)
        )
        self._local.pending_request = {
            key: customer_arguments.get(key) for key in ("operation", "period", "start", "end")
        }
        entity = CUSTOMER
        if routed:
            if row and row[0]:
                entity = row[0]
            elif picked:
                entity = picked[0]
            else:
                entity = self._party_entity(
                    directory, customer_arguments, model_entity, question, history
                )
            if isinstance(entity, BusinessAnswer):
                return self._business_answer(entity)
        if arguments.get("operation") == STATEMENT_OPERATION:
            return self._statement(entity, customer_arguments, context, today)
        answer = self._party_query(entity, customer_arguments, context, today, routed)
        if answer.text in ALL_NOT_FOUND_ANSWERS and single_customer and not chosen and not picked:
            dictated = self._dictated_code_answer(
                directory, customer_arguments, question, context, today, routed
            )
            if dictated is not None:
                answer = dictated
        if answer.text in ALL_NOT_FOUND_ANSWERS and not self._customer_missed:
            # One retry: the name may be an expense section, product or warehouse.
            self._customer_missed = True
            logger.info("dbagg stage=customer_not_found_retry")
            raise ToolHint(NOT_A_CUSTOMER_HINT)
        return self._business_answer(answer)

    def _party_query(self, entity, arguments, context, today, routed):
        if entity == SUPPLIER:
            validate_supplier_context(context)
            supplier_arguments = dict(arguments)
            supplier_arguments["supplier"] = supplier_arguments.pop("customer", None)
            return SupplierQueries(self.db, self.tables, today=today).execute(**supplier_arguments)
        if arguments.get("operation") in SUPPLIER_ONLY_OPERATIONS:
            return BusinessAnswer.make(
                "Ese reporte todavía solo está disponible para proveedores.",
                "needs_clarification",
                arguments["operation"],
                [],
            )
        if routed:
            validate_context(context, arguments.get("operation"))
        return CustomerQueries(self.db, self.tables, today=today).execute(**arguments)

    def _dictated_code_answer(self, directory, arguments, question, context, today, routed):
        """One retry of a dictated code («A. Robles tres» → A.ROBLES3), exact codes only.

        Never by similarity: the normalized value must equal an existing code in exactly one
        catalogue, which must agree with any entity the question states.
        """
        code = dictated_code(arguments.get("customer"))
        if code is None:
            return None
        found = directory.find(code, exact_code=True)
        entities = [entity for entity in (CUSTOMER, SUPPLIER) if found[entity]]
        explicit = explicit_entity(question, arguments.get("customer"))
        if explicit:
            entities = [entity for entity in entities if entity == explicit]
        if len(entities) != 1:
            return None
        logger.info("dbagg stage=dictated_code_resolved")
        self.last_trace["local_decision"] = "dictated_code"
        retried = {**arguments, "customer": code, "codigo_exacto": True}
        return self._party_query(entities[0], retried, context, today, routed)

    def _bank(self, arguments, context, today):
        """Accounts are resolved in their own catalogue, never against customers or suppliers."""
        operation = arguments["operation"]
        if BANKS not in self.tables:
            logger.info("dbagg stage=banks_not_enabled")
            return self._business_answer(
                BusinessAnswer.make(
                    "La consulta de bancos no está habilitada en este servicio.",
                    "unavailable",
                    operation,
                    [],
                )
            )
        validate_bank_context(context)
        bank_arguments = {
            k: v for k, v in arguments.items() if k in ("period", "start", "end", "limit")
        }
        # The model sometimes names the account in customer; it is still an account here.
        bank = arguments.get("banco") or arguments.get("customer")
        if operation == "saldos_bancos" and bank:
            raise ToolHint(
                "saldos_bancos incluye todas las cuentas y no admite una cuenta concreta. "
                "Si se pide el saldo de esa cuenta, usa saldo_banco con banco; "
                "si la intención no está clara, pide aclaración."
            )
        if operation != "saldos_bancos":
            bank_arguments["banco"] = bank
        queries = BankQueries(self.db, self.tables, today=today)
        answer = queries.execute(operation, **bank_arguments)
        if queries.report is not None:
            try:
                content = bank_report_pdf(queries.report)
            except Exception as exc:
                logger.warning("dbagg stage=bank_pdf_failed error_type=%s", type(exc).__name__)
                raise BusinessError("No pude generar el PDF del reporte de banco.") from None
            self._local.document = DocumentAttachment(content, bank_report_filename(queries.report))
            logger.info("dbagg stage=bank_report_ready rows=%s", len(queries.report.rows))
        return self._business_answer(answer)

    def _party_entity(self, directory, arguments, model_entity, question, history):
        """Which catalogue a question means, or the answer that asks; see docs/business-queries.md."""
        term = arguments.get("customer")
        explicit = explicit_entity(question, term)
        model = model_entity if model_entity in (CUSTOMER, SUPPLIER) else None
        if not isinstance(term, str) or not term.strip():
            return explicit or model or CUSTOMER
        term = term.strip()
        remembered = conversation_entity(term, history)
        if remembered and not explicit:
            return remembered
        found = directory.find(term, exact_code=arguments.get("codigo_exacto") is True)
        sources = [CUSTOMERS, SUPPLIERS]
        operation = arguments.get("operation")
        if explicit:
            other = CUSTOMER if explicit == SUPPLIER else SUPPLIER
            if (
                remembered == other
                and found[CUSTOMER]
                and found[SUPPLIER]
                and not names_entity(question, term)
            ):
                # «¿Y cuánto nos debe?» about a supplier in our favor is not a change of person.
                logger.info("dbagg stage=party_ambiguous")
                return BusinessAnswer.make(
                    ambiguous_answer(term, found), "needs_clarification", operation, sources
                )
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

    def _statement(self, entity, arguments, context, today):
        """Party first, then dates, then the document; nothing is generated before both."""
        if entity == SUPPLIER:
            validate_supplier_context(context)
        else:
            validate_context(context, STATEMENT_OPERATION)
        queries = StatementQueries(self.db, self.tables, today=today)
        found = queries.resolve(
            entity, arguments.get("customer"), exact_code=arguments.get("codigo_exacto") is True
        )
        if isinstance(found, BusinessAnswer):
            if found.text in ALL_NOT_FOUND_ANSWERS and not self._customer_missed:
                self._customer_missed = True
                logger.info("dbagg stage=customer_not_found_retry")
                raise ToolHint(NOT_A_CUSTOMER_HINT)
            return self._business_answer(found)
        name, code = label(found["nombre"]), label(found["codigo"])
        sources = [CUSTOMERS if entity == CUSTOMER else SUPPLIERS]
        period = arguments.get("period")
        if period in (None, "todo"):
            logger.info("dbagg stage=statement_needs_dates")
            text = (
                f"Proveedor {name} (código {code})\n¿De qué fechas quieres el estado de cuenta?"
                if entity == SUPPLIER
                else f"Estado de cuenta de {name} (código {code}). ¿De qué fechas lo quieres?"
            )
            return self._business_answer(
                BusinessAnswer.make(text, "needs_clarification", STATEMENT_OPERATION, sources)
            )
        try:
            first, until = period_bounds(
                period, today, arguments.get("start"), arguments.get("end")
            )
        except (ValueError, TypeError, OverflowError):
            raise BusinessError(
                "Indica un período válido; para un rango usa inicio y fin en formato YYYY-MM-DD."
            ) from None
        statement = queries.build(entity, found["codigo"], first, until - timedelta(days=1))
        try:
            content = statement_pdf(statement)
        except Exception as exc:
            logger.warning("dbagg stage=statement_pdf_failed error_type=%s", type(exc).__name__)
            raise BusinessError("No pude generar el PDF del estado de cuenta.") from None
        self._local.document = DocumentAttachment(content, statement_filename(statement))
        identity = (
            f"Proveedor {name} (código {code})"
            if entity == SUPPLIER
            else f"Estado de cuenta de {name} (código {code})"
        )
        lines = [
            identity,
            f"Estado de cuenta del {statement.start:%d/%m/%Y} al {statement.end:%d/%m/%Y}.",
            f"Saldo anterior: {amount(statement.opening)}.",
            f"Saldo al {statement.end:%d/%m/%Y}: {amount(statement.closing)}.",
            f"Movimientos: {len(statement.rows)}.",
            *statement.warnings,
            "Importes sin moneda confirmada.",
        ]
        logger.info("dbagg stage=statement_ready rows=%s", len(statement.rows))
        return self._business_answer(
            BusinessAnswer.make(
                "\n".join(lines),
                "partial" if statement.warnings else "answered",
                STATEMENT_OPERATION,
                queries.sources,
            )
        )

    def pop_document(self):
        """The document of the latest answer, once; the conversation delivers it."""
        document = getattr(self._local, "document", None)
        self._local.document = None
        return document

    def _clarifications(self):
        if SUPPLIERS in self.tables:
            return {**CLARIFICATIONS, **SUPPLIER_CLARIFICATIONS}
        return CLARIFICATIONS

    def _business_answer(self, answer):
        pending = (
            getattr(self._local, "pending_request", None)
            if answer.status == "needs_clarification"
            else None
        )
        legacy = getattr(self._local, "legacy_state", None)
        if (
            legacy is not None
            and answer.metadata.get("entity") in {"cliente", "proveedor"}
            and answer.metadata.get("code")
        ):
            # A verified legacy result keeps domain, operation, identity and period confirmed
            # by the service; nothing proposed only by the model is stored.
            self._local.next_state = legacy.for_route("clientes." + answer.operation).after_result(
                answer.metadata, answer.status
            )
        else:
            self._local.next_state = self.next_state.after_result(
                answer.metadata, answer.status, pending
            )
        self.last_trace.update(
            status=answer.status,
            operation=answer.operation,
            sources=answer.sources,
            evidence_id=answer.evidence_id,
        )
        logger.info("dbagg stage=business_answer status=%s", answer.status)
        return answer.text

    @stage("schema")
    def _describe_tables(self, selected, described):
        if not isinstance(selected, list) or not 1 <= len(selected) <= 5:
            raise ValueError("Describe entre uno y cinco objetos.")
        if any(not isinstance(table, str) for table in selected):
            raise ValueError("Los nombres de objetos deben ser texto.")
        selected = [table.lower() for table in selected]
        allowed = getattr(self._local, "route_tables", None)
        allowed = self.tables if allowed is None else allowed
        if not set(selected).issubset(allowed):
            raise ValueError("Objetos no autorizados.")
        logger.info("dbagg stage=schema_start")
        result = self.catalog.filter_schema(json.loads(self.db.schema(selected)))
        described.update(selected)
        logger.info("dbagg stage=schema_ok")
        return result

    @stage("sql")
    def _query_sql(self, statement, described):
        allowed = getattr(self._local, "route_tables", None)
        allowed = self.tables if allowed is None else allowed
        sql = validate_sql(statement, allowed, self.catalog)
        referenced = {
            f"{table.db}.{table.name}".lower()
            for table in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)
        }
        if referenced & (PROTECTED_SOURCES | SUPPLIER_PROTECTED_SOURCES | BANK_PROTECTED_SOURCES):
            raise ValueError("Estas fuentes requieren las operaciones de negocio verificadas.")
        if not referenced.issubset(described):
            raise ValueError("Primero inspecciona las columnas de los objetos usados.")
        logger.info("dbagg stage=sql_validated")
        result = self.db.query(sql)
        logger.info("dbagg stage=query_ok")
        return result, sorted(referenced)

    @stage("render")
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
