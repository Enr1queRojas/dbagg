"""Modular execution must narrow context without changing business or SQL grants."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from dbagg.agent.context_builder import select_context, validate_tool_call
from dbagg.agent.prompt_files import load_prompt_bundle
from dbagg.agent.service import Assistant
from dbagg.context.loader import load_business_context
from dbagg.database.catalog import load_catalog
from dbagg.llm.config import LLMConfig, ModelProfile, ModularConfig, load_llm_config
from dbagg.llm.types import ModelReply, ToolCall
from dbagg.routing.contracts import RoutingDecision
from dbagg.routing.jev import JevRouter
from dbagg.routing.registry import available_routes, route_options
from dbagg.routing.service import ReferenceRouter
from tests.unit.test_customer_queries import SyntheticDatabase, CUSTOMERS, PAYMENTS


def reply(name, arguments):
    call = ToolCall("test", name, json.dumps(arguments))
    return ModelReply(
        None,
        (call,),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": name, "arguments": call.arguments},
                }
            ],
        },
        {"prompt_tokens": 10},
    )


class ModularContextTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.tables = frozenset(self.catalog.sources)
        self.routes = available_routes(self.catalog, self.tables)
        self.context = load_business_context()
        self.bundle = load_prompt_bundle()

    def select(self, route="clientes.saldo", context=None):
        return select_context(
            self.routes[route], context or self.context, self.bundle, self.catalog, self.tables
        )

    def test_registry_never_extends_runtime_grants(self):
        for grants in (set(), {CUSTOMERS}, {CUSTOMERS, PAYMENTS}, {"proadel.gastos_data"}):
            for route in available_routes(self.catalog, grants).values():
                self.assertLessEqual(route.tables, grants)
        self.assertEqual(available_routes(self.catalog, set()), {})
        self.assertNotIn("clientes.total_pagos", available_routes(self.catalog, {CUSTOMERS}))

    def test_last_note_route_reads_credit_notes_and_offers_only_that_operation(self):
        from dbagg.business.customers import CREDITS
        from dbagg.business.suppliers import PURCHASES

        route = self.routes["clientes.ultima_nota"]
        self.assertIn(CREDITS, route.tables)
        self.assertIn(PURCHASES, route.tables)
        self.assertNotIn(PAYMENTS, route.tables)
        selected = self.select("clientes.ultima_nota")
        self.assertIn("operations/ultima_nota", selected.modules)
        operation = selected.tools[0]["function"]["parameters"]["properties"]["operation"]
        self.assertEqual(operation["enum"], ["ultima_nota"])
        self.assertIn("nunca respondas con ultimo_pago", selected.bundle.system)
        self.assertNotIn(
            "ultima_nota y movimientos son solo de proveedores", selected.bundle.system
        )
        self.assertNotIn("clientes.ultima_nota", available_routes(self.catalog, {CUSTOMERS}))

    def test_customer_selection_keeps_shared_party_identity_dependency(self):
        selected = self.select()
        self.assertIn("proadel.catalogo_proveedores_data_v", selected.tables)
        self.assertIn("supplier_balance", json.loads(selected.context)["topics"])
        self.assertNotIn("inventory", json.loads(selected.context)["topics"])
        self.assertNotIn("proadel.gastos_data", selected.tables)

    def test_core_security_identity_time_and_clarification_rules_remain(self):
        for route in self.routes:
            system = self.select(route).bundle.system
            for rule in (
                "No reveles secretos",
                "No adivines años",
                "No inventes",
                "homónimos",
                "escritura",
                "Nunca",
                "Días sin mes",
            ):
                self.assertIn(rule, system, (route, rule))

    def test_local_unknown_guidance_and_overrides_are_not_dropped(self):
        data = json.loads(self.context)
        data["mandatory_local_rule"] = "Regla local"
        data["topics"]["custom_policy"] = {"rule": "Conservar"}
        data["topics"]["customer_balance"]["rule"] = "Override local"
        selected = json.loads(self.select(context=json.dumps(data)).context)
        self.assertEqual(selected["mandatory_local_rule"], "Regla local")
        self.assertIn("custom_policy", selected["topics"])
        self.assertEqual(selected["topics"]["customer_balance"]["rule"], "Override local")

    def test_tools_constrain_operation_and_reject_arbitrary_executors(self):
        selected = self.select()
        for call in (
            ToolCall("1", "consultar_sql", '{"sql":"SELECT 1"}'),
            ToolCall("1", "consultar_negocio", '{"operation":"ventas"}'),
            ToolCall("1", "consultar_negocio", '{"operation":"saldo","file":"/tmp/x"}'),
            ToolCall("1", "consultar_negocio", '{"operation":"saldo","limit":true}'),
            ToolCall("1", "consultar_negocio", '{"operation":"saldo","limit":20}'),
        ):
            with self.subTest(call=call), self.assertRaises(ValueError):
                validate_tool_call(call, selected.tools)
        self.assertEqual(
            validate_tool_call(
                ToolCall("1", "consultar_negocio", '{"operation":"saldo","customer":"Demo"}'),
                selected.tools,
            )["customer"],
            "Demo",
        )

    def test_expense_context_and_tools_exclude_other_domains(self):
        selected = self.select("gastos.exploracion")
        self.assertNotIn("consultar_negocio", {t["function"]["name"] for t in selected.tools})
        self.assertTrue(all(self.catalog.sources[t].domain == "gastos" for t in selected.tables))
        self.assertIn("exploratorio", selected.bundle.system)

    def test_fingerprint_is_stable_and_changes_with_local_guidance(self):
        self.assertEqual(self.select().bundle.version, self.select().bundle.version)
        data = json.loads(self.context)
        data["new_rule"] = "Cambió"
        self.assertNotEqual(
            self.select().bundle.version, self.select(context=json.dumps(data)).bundle.version
        )


class ModularAgentTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.provider = Mock()
        self.config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=self.config),
            db=self.db,
            provider=self.provider,
        )
        self.addCleanup(self.assistant.close)

    def route(self, operation="saldo"):
        return reply("select_route", {"route": "clientes." + operation})

    def balance(self, **kwargs):
        return reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo", **kwargs})

    def test_real_business_result_and_selected_tools(self):
        self.provider.complete.side_effect = [self.route(), self.balance()]
        answer = self.assistant.answer("Saldo de Ana Demo")
        self.assertIn("123.45", answer)
        self.assertEqual(self.assistant.last_trace["route"], "clientes.saldo")
        messages, tools = self.provider.complete.call_args.args
        self.assertNotIn("inventarios", messages[0]["content"])
        self.assertEqual(
            tools[0]["function"]["parameters"]["properties"]["operation"]["enum"], ["saldo"]
        )
        self.assertEqual(
            len([e for e in self.assistant.last_trace["events"] if e["event"] == "usage"]), 2
        )

    def test_invented_entity_never_reaches_sql(self):
        self.provider.complete.side_effect = [self.route(), self.balance(customer="Inventado")]
        self.assertIn("No pude vincular", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(self.db.calls, [])

    def test_route_does_not_authorize_different_operation_or_sql(self):
        for bad in (
            reply("consultar_negocio", {"operation": "ventas"}),
            reply("consultar_sql", {"sql": "DELETE FROM proadel.cobranza_data"}),
        ):
            self.provider.complete.side_effect = [self.route(), bad, bad]
            self.assistant.answer("Saldo de Ana Demo")
            self.assertEqual(self.db.calls, [])

    def test_ambiguity_still_asks_user_instead_of_selecting_first(self):
        self.db.customer("C2", "Ana Demo Otra", "999")
        self.provider.complete.side_effect = [self.route(), self.balance()]
        answer = self.assistant.answer("Saldo de Ana Demo")
        self.assertIn("C1", answer)
        self.assertIn("C2", answer)
        self.assertEqual(self.assistant.last_trace["status"], "needs_clarification")

    def test_reference_failure_returns_to_legacy(self):
        self.provider.complete.side_effect = [ValueError("Bad route"), self.balance()]
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertNotIn("route", self.assistant.last_trace)

    def test_shadow_does_not_narrow_execution(self):
        self.assistant.llm_config = replace(
            self.config, modular=ModularConfig(enabled=True, shadow=True)
        )
        self.provider.complete.side_effect = [self.route(), self.balance()]
        self.assistant.answer("Saldo de Ana Demo")
        self.assertIsNone(self.assistant._local.route_tables)
        self.assertNotIn("route", self.assistant.last_trace)
        tools = self.provider.complete.call_args.args[1]
        self.assertIn("consultar_sql", {t["function"]["name"] for t in tools})

    def test_failed_jev_and_failed_reference_get_only_one_fallback(self):
        router = Mock()
        router.decide.side_effect = RuntimeError("timeout")
        self.assistant.router = router
        self.provider.complete.side_effect = [ValueError("invalid reference"), self.balance()]
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(router.decide.call_count, 1)
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_low_confidence_and_reference_failure_does_not_retry_classification(self):
        self.assistant.llm_config = replace(
            self.config, modular=ModularConfig(enabled=True, min_confidence=0.8)
        )
        self.assistant.router = Mock()
        self.assistant.router.decide.return_value = RoutingDecision("clientes.saldo", "jev", 0.1)
        self.provider.complete.side_effect = [ValueError("invalid reference"), self.balance()]
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(self.provider.complete.call_count, 2)

    def test_extraction_failure_gets_one_reference_attempt(self):
        nano = Mock()
        nano.complete.side_effect = RuntimeError("private text must not be logged")
        self.assistant.extraction_provider = nano
        self.provider.complete.side_effect = [self.route(), self.balance()]
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(nano.complete.call_count, 1)
        self.assertEqual(self.provider.complete.call_count, 2)
        self.assertNotIn("private text", json.dumps(self.assistant.last_trace))

    def test_malformed_extraction_and_reference_never_loop(self):
        nano = Mock()
        nano.complete.return_value = reply("consultar_negocio", {"operation": "ventas"})
        self.assistant.extraction_provider = nano
        self.provider.complete.side_effect = [
            self.route(),
            reply("consultar_negocio", {"operation": "ventas"}),
        ]
        self.assistant.answer("Saldo de Ana Demo")
        self.assertEqual(nano.complete.call_count, 1)
        self.assertEqual(self.provider.complete.call_count, 2)
        self.assertEqual(self.db.calls, [])

    def test_missing_period_is_a_clarification_without_extraction_retry(self):
        nano = Mock()
        nano.complete.return_value = reply("pedir_aclaracion", {"reason": "periodo"})
        self.assistant.extraction_provider = nano
        self.provider.complete.return_value = self.route("total_pagos")
        answer = self.assistant.answer("Pagos de Ana Demo en octubre")
        self.assertIn("período", answer)
        self.assertEqual(self.provider.complete.call_count, 1)
        self.assertEqual(nano.complete.call_count, 0)
        self.assertEqual(self.db.calls, [])

    def test_multiple_calls_cannot_partially_execute(self):
        a, b = self.balance(), reply("pedir_aclaracion", {"reason": "criterio"})
        self.provider.complete.side_effect = [
            self.route(),
            replace(a, tool_calls=(a.tool_calls[0], b.tool_calls[0])),
        ]
        self.assistant.answer("Saldo y pagos de Ana Demo")
        self.assertEqual(self.db.calls, [])

    def test_expense_sql_cannot_describe_or_read_other_domains(self):
        self.assistant._local.route_tables = frozenset({"proadel.gastos_data"})
        with self.assertRaises(ValueError):
            self.assistant._describe_tables([CUSTOMERS], set())
        with self.assertRaises(ValueError):
            self.assistant._query_sql(
                "SELECT CODIGO FROM proadel.catalogo_clientes_data_v", {CUSTOMERS}
            )
        self.assertEqual(self.db.calls, [])


class JevTests(unittest.TestCase):
    def setUp(self):
        self.routes = available_routes(load_catalog(), {CUSTOMERS, PAYMENTS})
        self.options = route_options(self.routes)

    def router(self, payload):
        def handle(request):
            self.request = request
            return httpx.Response(200, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return JevRouter(ModularConfig(), "synthetic-secret", client)

    def payload(self):
        return {
            "model": "jev-1.14.0",
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "clientes.saldo",
                    "confidence": 0.94,
                    "probabilities": {
                        name: float(name == "clientes.saldo") for name in self.options
                    },
                }
            },
            "usage": {"input_tokens": 42, "output_tokens": 2},
        }

    def test_documented_contract_preserves_probabilities_and_usage(self):
        router = self.router(self.payload())
        decision = router.decide("Saldo de Demo", [], self.routes)
        self.assertEqual(decision.confidence, 0.94)
        self.assertEqual(decision.usage["input_tokens"], 42)
        self.assertEqual(self.request.url.path, "/v1/systemone")
        body = json.loads(self.request.content)
        self.assertEqual(body["questions"]["route"]["type"], "choice")
        self.assertNotIn("schema", body["state"])
        self.assertEqual(self.request.headers["Authorization"], "Bearer synthetic-secret")

    def test_invalid_route_confidence_distribution_are_rejected(self):
        for key, value in (
            ("choice", "../../executor"),
            ("confidence", True),
            ("confidence", float("inf")),
            ("probabilities", {}),
        ):
            with self.subTest(key=key, value=value):
                data = self.payload()
                data["answers"]["route"][key] = value
                # JSON transport rejects Infinity before the parser; use NaN-free boundary instead.
                if key == "confidence" and type(value) is float:
                    data["answers"]["route"][key] = 1.1
                with self.assertRaises(ValueError):
                    self.router(data).decide("Saldo", [], self.routes)

    def test_missing_key_never_sends_request(self):
        router = self.router(self.payload())
        router.api_key = ""
        with self.assertRaises(ValueError):
            router.decide("Saldo", [], self.routes)
        self.assertFalse(hasattr(self, "request"))

    def test_reference_rejects_unregistered_identifier(self):
        provider = Mock()
        provider.complete.return_value = reply("select_route", {"route": "unregistered"})
        with self.assertRaises(ValueError):
            ReferenceRouter(provider).decide("Pregunta", [], self.routes)


class ModularConfigTests(unittest.TestCase):
    def test_default_compatibility_and_strict_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_llm_config(root, {})
            self.assertFalse(config.modular.enabled)
            base = Path("src/dbagg/llm/default.toml").read_text()
            path = root / "llm.toml"
            for section in (
                '[modular]\nenabled=true\nrouter="jev"',
                '[modular]\nenabled="yes"',
                '[modular]\ndomains=["unknown"]',
                '[modular]\nextraction_profile="missing"',
                '[modular]\napi_key="secret"',
            ):
                path.write_text(base + "\n" + section)
                with self.subTest(section=section), self.assertRaises(ValueError):
                    load_llm_config(root, {"LLM_CONFIG": str(path)})
            path.write_text(base + '\n[modular]\nenabled=true\nrouter="jev"\nshadow=true\n')
            self.assertTrue(load_llm_config(root, {"LLM_CONFIG": str(path)}).modular.shadow)


class ExplicitStateTests(unittest.TestCase):
    def test_validated_identity_survives_operation_change_and_domain_change_clears_it(self):
        from dbagg.routing.state import ConversationState

        state = (
            ConversationState()
            .for_route("clientes.saldo")
            .after_result({"code": "C1", "entity": "cliente", "period": "todo"}, "answered")
        )
        self.assertEqual(state.for_route("clientes.total_pagos").selected_customer_id, "C1")
        changed = state.for_route("gastos.exploracion")
        self.assertIsNone(changed.selected_customer_id)
        self.assertIsNone(changed.period)
        self.assertIsNone(changed.pending_clarification)

    def test_ambiguous_result_does_not_keep_a_previous_customer(self):
        from dbagg.routing.state import ConversationState

        state = ConversationState(selected_customer_id="C1", entity="cliente")
        self.assertIsNone(state.after_result({}, "needs_clarification").selected_customer_id)

    def test_memory_isolates_expires_and_resets_state_with_history(self):
        from dbagg.routing.state import ConversationState
        from dbagg.services.memory import ConversationMemory
        from unittest.mock import patch

        memory = ConversationMemory()
        state = ConversationState(selected_customer_id="C1", previous_route="clientes.saldo")
        with patch("dbagg.services.memory.time.monotonic", return_value=0):
            memory.remember("one", "q", "a", state=state)
            self.assertEqual(memory.state("one"), state)
            self.assertIsNone(memory.state("two").selected_customer_id)
            memory.clear("one")
            self.assertIsNone(memory.state("one").selected_customer_id)
            memory.remember("one", "q", "a", state=state)
        with patch("dbagg.services.memory.time.monotonic", return_value=1801):
            self.assertIsNone(memory.state("one").selected_customer_id)
            self.assertEqual(memory.history("one"), [])

    def test_only_resolved_business_results_set_identity(self):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer()
        provider = Mock()
        provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        ]
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=db,
            provider=provider,
        )
        assistant.answer("Saldo de Ana Demo")
        self.assertEqual(assistant.next_state.selected_customer_id, "C1")
        # Identifiers do not enter usage events.
        self.assertNotIn('"C1"', json.dumps(assistant.last_trace["events"]))

    def test_trace_and_retry_flags_are_isolated_between_threads(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS}), db=Mock(), provider=Mock()
        )
        barrier = threading.Barrier(2)

        def work(value):
            assistant.last_trace = {"marker": value}
            assistant._customer_missed = bool(value)
            barrier.wait(timeout=5)
            return assistant.last_trace["marker"], assistant._customer_missed

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(work, (0, 1))), [(0, False), (1, True)])


class CostTests(unittest.TestCase):
    def test_cached_input_is_counted_once_and_unknown_usage_is_preserved(self):
        from dbagg.evaluation.cost import estimate_event, estimate_events

        event = {
            "event": "usage",
            "model": "gpt-4.1-mini",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 800},
            },
        }
        self.assertAlmostEqual(estimate_event(event), (200 * 0.4 + 800 * 0.1 + 10 * 1.6) / 1e6)
        report = estimate_events([event, {"event": "usage", "usage": None}])
        self.assertEqual(report["unknown_attempts"], 1)
        self.assertFalse(report["complete"])
        event["usage"]["prompt_tokens_details"] = {}
        self.assertIsNone(estimate_event(event))


class ModularEndToEndTests(unittest.TestCase):
    def test_followup_uses_confirmed_customer_and_executes_payment_contract(self):
        from datetime import date
        from unittest.mock import patch

        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer()
        db.payment(1, "2026-10-05", 100)
        db.payment(2, "2026-10-06", -10)
        db.payment(3, "2026-10-06", 999, "CANCELADA")
        provider = Mock()
        provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
            reply("select_route", {"route": "clientes.total_pagos"}),
            reply(
                "consultar_negocio",
                {
                    "operation": "total_pagos",
                    "customer": "C1",
                    "codigo_exacto": True,
                    "period": "este_mes",
                },
            ),
        ]
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=db,
            provider=provider,
        )
        with patch("dbagg.agent.service.business_today", return_value=date(2026, 10, 8)):
            answer = assistant.answer("Saldo de Ana Demo")
            state = assistant.next_state
            history = [
                {"role": "user", "content": "Saldo de Ana Demo"},
                {"role": "assistant", "content": answer},
            ]
            paid = assistant.answer(
                "¿Y cuánto ha pagado este mes?", history=history, conversation_state=state
            )
        self.assertIn("90.00", paid)
        self.assertEqual(assistant.next_state.selected_customer_id, "C1")
        self.assertEqual(assistant.next_state.previous_route, "clientes.total_pagos")
        self.assertEqual(assistant.next_state.period, "este_mes")

    def test_expenses_describe_locally_then_validate_sql_and_render_evidence(self):
        db = Mock()
        db.schema.return_value = json.dumps(
            [
                {
                    "table": "proadel.gastos_data",
                    "columns": [
                        {"name": "IMPORTE", "type": "decimal"},
                        {"name": "ESTADO", "type": "nvarchar"},
                    ],
                }
            ]
        )
        db.query.return_value = {"columns": ["importe"], "rows": [["90.00"]]}
        provider = Mock()
        provider.complete.side_effect = [
            reply("select_route", {"route": "gastos.exploracion"}),
            reply(
                "consultar_sql",
                {
                    "sql": "SELECT SUM(IMPORTE) AS importe FROM proadel.GASTOS_DATA WHERE ESTADO = 'ACTIVA'"
                },
            ),
            ModelReply("Fictional 999", (), {"role": "assistant", "content": "Fictional 999"}),
        ]
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        assistant = Assistant(
            SimpleNamespace(model="test", tables={"proadel.gastos_data"}, llm=config),
            db=db,
            provider=provider,
        )
        answer = assistant.answer("Dame los gastos registrados")
        db.schema.assert_called_once()
        db.query.assert_called_once()
        self.assertIn("90.00", answer)
        self.assertNotIn("999", answer)
        self.assertEqual(assistant.last_trace["status"], "exploratory")

    def test_greeting_does_not_consume_a_business_question(self):
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        provider = Mock()
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS}, llm=config),
            db=Mock(),
            provider=provider,
        )
        self.assertIn("Hola", assistant.answer("¡Hola!"))
        provider.complete.assert_not_called()
        provider.complete.side_effect = [
            reply("select_route", {"route": "legacy"}),
            reply("pedir_aclaracion", {"reason": "cliente"}),
        ]
        assistant.answer("Hola, ¿cuánto debe?")
        self.assertEqual(provider.complete.call_count, 2)


class CalendarContinuityTests(unittest.TestCase):
    def test_unrelated_year_is_not_inherited_after_domain_change(self):
        from dbagg.routing.policy import needs_expense_year
        from dbagg.routing.state import ConversationState

        state = ConversationState(
            previous_route="clientes.total_pagos",
            period="rango",
            start="2025-10-01",
            end="2025-10-31",
        )
        history = [{"role": "user", "content": "Pagos de Ana en octubre de 2025"}]
        self.assertTrue(
            needs_expense_year("Gastos en octubre", history, state.for_route("gastos.exploracion"))
        )
        self.assertFalse(needs_expense_year("¿Y septiembre?", history, state))
        self.assertTrue(needs_expense_year("Gastos en octubre", history, state))


class ExpenseTopicChangeTests(unittest.TestCase):
    def test_explicit_bank_question_restores_full_tools_after_customer_route(self):
        from dbagg.business.banks import BANK_SOURCES
        from tests.unit.test_bank_queries import BankDatabase

        db = BankDatabase()
        self.addCleanup(db.conn.close)
        db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")
        provider = Mock()
        provider.complete.return_value = reply(
            "consultar_negocio", {"operation": "saldo_banco", "banco": "ENRIQUE"}
        )
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, router="jev", min_confidence=0.8)
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS, *BANK_SOURCES}, llm=config),
            db=db,
            provider=provider,
        )
        assistant.router.close()
        assistant.router = Mock()
        assistant.router.decide.return_value = RoutingDecision("clientes.saldo", "jev", 0.99)
        history = [
            {"role": "user", "content": "Saldo de Esparza"},
            {"role": "assistant", "content": "Saldo de ALEJANDRO ESPARZA: 59,850.60"},
        ]
        for question in ("El banco de Enrique", "Dame el saldo del Banco Enrique Rojas"):
            with self.subTest(question=question):
                answer = assistant.answer(question, history=history)
                self.assertIn("Saldo actual: -20.00", answer)
                self.assertEqual(assistant.last_trace["modular_fallback"], "route_inconsistent")

    def test_jev_cannot_keep_customer_payments_after_explicit_expense_question(self):
        from scripts.benchmark_usage import DemoDatabase, EXPENSES

        db = DemoDatabase()
        self.addCleanup(db.conn.close)
        provider = Mock()
        provider.complete.side_effect = [
            reply("describir_tablas", {"tables": [EXPENSES]}),
            reply(
                "consultar_sql",
                {"sql": f"SELECT SUM(IMPORTE) AS total FROM {EXPENSES} WHERE ESTADO = 'ACTIVA'"},
            ),
            ModelReply(None, (), {"role": "assistant", "content": None}),
        ]
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, router="jev", min_confidence=0.8)
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS, EXPENSES}, llm=config),
            db=db,
            provider=provider,
        )
        assistant.router.close()
        assistant.router = Mock()
        assistant.router.decide.return_value = RoutingDecision("clientes.total_pagos", "jev", 0.99)
        history = [
            {"role": "user", "content": "¿Y cuánto ha pagado en total?"},
            {
                "role": "assistant",
                "content": "Total de pagos activos de Cliente Demo (código DEMO1): 25.00.",
            },
        ]
        answer = assistant.answer("¿Cuál es el total de gastos registrados?", history=history)
        self.assertIn("42", answer)
        self.assertNotIn("999", answer)
        self.assertEqual(assistant.last_trace["status"], "exploratory")
        self.assertEqual(assistant.last_trace["modular_fallback"], "route_inconsistent")
        self.assertTrue(all(EXPENSES in sql for sql, _, _ in db.calls))
        self.assertTrue(
            any(
                e.get("rejected_route") == "clientes.total_pagos"
                for e in assistant.last_trace["events"]
            )
        )

    def test_veto_does_not_override_ambiguous_requests_or_bank_routing(self):
        from dbagg.routing.contracts import Route
        from dbagg.routing.policy import route_is_consistent

        customers = available_routes(load_catalog(), {CUSTOMERS, PAYMENTS})
        for operation in customers.values():
            self.assertFalse(
                route_is_consistent("¿Cuál es el total de gastos registrados?", operation)
            )
        self.assertTrue(
            route_is_consistent("¿Y cuánto ha pagado en total?", customers["clientes.total_pagos"])
        )
        bank = Route(
            "bancos.movimientos", "bancos", "movimientos_banco", "", (), frozenset(), (), "business"
        )
        self.assertTrue(route_is_consistent("¿Qué gastos salieron de caja chica?", bank))
