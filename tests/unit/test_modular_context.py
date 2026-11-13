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
from dbagg.agent.prompt_files import TEMPLATES, load_prompt_bundle
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

    def test_domain_module_with_several_operations(self):
        single = self.select("clientes.saldo")
        listed = select_context(
            [self.routes["clientes.saldo"]], self.context, self.bundle, self.catalog, self.tables
        )
        self.assertEqual(listed.bundle.version, single.bundle.version)
        self.assertEqual(listed.tools, single.tools)
        names = ["clientes.saldo", "clientes.total_pagos", "clientes.ultimo_pago"]
        several = select_context(
            [self.routes[n] for n in names], self.context, self.bundle, self.catalog, self.tables
        )
        self.assertEqual(
            several.modules,
            (
                "core",
                "shared/identity",
                "shared/time",
                "domains/customers",
                "operations/saldo",
                "operations/total_pagos",
                "operations/ultimo_pago",
                "shared/wrong_route",
            ),
        )
        operation = several.tools[0]["function"]["parameters"]["properties"]["operation"]
        self.assertEqual(operation["enum"], ["saldo", "total_pagos", "ultimo_pago"])
        self.assertEqual(several.tables, frozenset().union(*(self.routes[n].tables for n in names)))
        topics = set(json.loads(several.context)["topics"])
        self.assertTrue({"customer_balance", "collections"} <= topics)
        self.assertNotIn("producto", several.tools[0]["function"]["parameters"]["properties"])
        with self.assertRaises(ValueError):
            select_context(
                [self.routes["clientes.saldo"], self.routes["gastos.exploracion"]],
                self.context,
                self.bundle,
                self.catalog,
                self.tables,
            )

    def test_customer_selection_keeps_shared_party_identity_dependency(self):
        selected = self.select()
        self.assertIn("proadel.catalogo_proveedores_data_v", selected.tables)
        self.assertIn("supplier_balance", json.loads(selected.context)["topics"])
        self.assertNotIn("inventory", json.loads(selected.context)["topics"])
        self.assertNotIn("proadel.gastos_data", selected.tables)

    def test_core_security_identity_time_and_clarification_rules_remain(self):
        # Identity rules belong to party routes; every route keeps security, time and
        # clarification rules (owner decision when splitting core.md, T14).
        for route in self.routes:
            system = self.select(route).bundle.system
            rules = ["No reveles secretos", "No adivines años", "No inventes", "escritura"]
            rules += ["Nunca", "Días sin mes"]
            if self.routes[route].domain == "clientes":
                rules.append("homónimos")
            for rule in rules:
                self.assertIn(rule, system, (route, rule))

    def test_split_core_moves_text_without_losing_rules(self):
        before = (Path(__file__).parent / "data" / "core_before_split.md").read_text(
            encoding="utf-8"
        )
        party = self.select("clientes.saldo")
        self.assertEqual(
            party.modules[:4], ("core", "shared/identity", "shared/time", "domains/customers")
        )
        lines = set(party.bundle.system.splitlines())
        missing = [line for line in before.splitlines() if line.strip() and line not in lines]
        self.assertEqual(missing, [])
        expenses = self.select("gastos.exploracion")
        self.assertIn("shared/time", expenses.modules)
        self.assertNotIn("shared/identity", expenses.modules)
        for rule in ("# Identidad y continuidad", "codigo_exacto", "homónimos"):
            self.assertNotIn(rule, expenses.bundle.system)

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


class PreRouteTests(unittest.TestCase):
    """Questions whose route is already known never pay for a classification."""

    def test_policy_recognizes_short_replies_banks_and_named_accounts(self):
        from dbagg.routing.policy import pre_route

        for question in ("sí", "No.", "ok", "¡Gracias!", "El banco de Enrique", "bancos"):
            with self.subTest(question=question):
                self.assertEqual(pre_route(question), "legacy")
        names = ("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS")
        self.assertEqual(pre_route("¿Cuánto efectivo tiene Enrique?", bank_names=names), "legacy")
        self.assertEqual(pre_route("la cuenta de enrique", bank_names=names), "legacy")
        for question in ("¿Cuánto debe Enrique?", "sí, el saldo de Ana", "efectivo de Ana"):
            with self.subTest(question=question):
                self.assertIsNone(pre_route(question, bank_names=names))

    def assistant(self):
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
        return assistant

    def test_known_legacy_questions_make_no_routing_call_and_keep_the_answer(self):
        assistant = self.assistant()
        for question in (
            "El banco de Enrique",
            "Dame el saldo del Banco Enrique Rojas",
            "¿Cuánto efectivo tiene Enrique?",
        ):
            with self.subTest(question=question):
                answer = assistant.answer(question, history=[])
                self.assertIn("Saldo actual: -20.00", answer)
                self.assertEqual(assistant.last_trace["local_decision"], "pre_route_legacy")
                self.assertFalse(
                    any(e.get("task") == "routing" for e in assistant.last_trace.get("events", []))
                )
        assistant.router.decide.assert_not_called()

    def test_short_reply_makes_no_routing_call(self):
        assistant = self.assistant()
        assistant.provider.complete.return_value = reply("pedir_aclaracion", {"reason": "criterio"})
        assistant.answer("ok", history=[])
        assistant.router.decide.assert_not_called()
        self.assertEqual(assistant.last_trace["local_decision"], "pre_route_legacy")

    def test_other_questions_still_route_once_without_reading_account_names(self):
        assistant = self.assistant()
        statements = []
        query = assistant.db.query

        def recorded(sql, *args, **kwargs):
            statements.append(sql)
            return query(sql, *args, **kwargs)

        assistant.db.query = recorded
        assistant.provider.complete.return_value = reply("pedir_aclaracion", {"reason": "cliente"})
        assistant.answer("¿Cuánto debe?", history=[])
        assistant.router.decide.assert_called_once()
        self.assertFalse(any("CUENTA BANCARIA" in sql for sql in statements))


class PendingClarificationTests(unittest.TestCase):
    """Answers to the service's own «¿cuál?» or «¿qué período?» need no model."""

    LIST = (
        "Encontré varios clientes:\n• ANA ROBLES (código A.ROBLES1)\n"
        "• LUIS ROBLES (código L.ROBLES2)\n• MARIO ROBLES (código M.ROBLES3)\n"
        "¿Cuál quieres consultar?"
    )

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        for code, name in (
            ("A.ROBLES1", "ANA ROBLES"),
            ("L.ROBLES2", "LUIS ROBLES"),
            ("M.ROBLES3", "MARIO ROBLES"),
        ):
            self.db.customer(code, name, "300")
        self.db.payment(1, "2026-10-02", "40", customer="M.ROBLES3")
        self.provider = Mock()
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=self.provider,
        )

    def listed(self):
        """First turn through the real modular flow: the service lists the homonyms."""
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.total_pagos"}),
            reply(
                "consultar_negocio",
                {"operation": "total_pagos", "customer": "Robles", "period": "este_mes"},
            ),
        ]
        from unittest.mock import patch
        from datetime import date

        with patch("dbagg.agent.service.business_today", return_value=date(2026, 10, 8)):
            text = self.assistant.answer("¿Cuánto pagó Robles este mes?")
        self.assertEqual(text, self.LIST)
        state = self.assistant.next_state
        self.assertEqual(state.pending_clarification, "cliente")
        self.assertEqual(state.pending_operation, "total_pagos")
        self.assertEqual(state.period, "este_mes")
        history = [
            {"role": "user", "content": "¿Cuánto pagó Robles este mes?"},
            {"role": "assistant", "content": text},
        ]
        self.provider.reset_mock()
        self.provider.complete.side_effect = None
        self.provider.complete.return_value = reply("pedir_aclaracion", {"reason": "criterio"})
        return history, state

    def answer(self, question, history, state):
        from unittest.mock import patch
        from datetime import date

        with patch("dbagg.agent.service.business_today", return_value=date(2026, 10, 8)):
            return self.assistant.answer(question, history, state)

    def test_position_or_code_of_the_list_runs_the_pending_operation(self):
        for question in ("3", "el tercero", "M.ROBLES3", "m.robles3"):
            with self.subTest(question=question):
                history, state = self.listed()
                text = self.answer(question, history, state)
                self.provider.complete.assert_not_called()
                self.assertIn("Total de pagos activos de MARIO ROBLES (código M.ROBLES3)", text)
                self.assertIn("40.00", text)
                self.assertIn("Del 01/10/2026", text)
                self.assertEqual(
                    self.assistant.last_trace["local_decision"], "pending_clarification"
                )

    def test_other_wording_uses_the_normal_flow(self):
        for question in ("7", "Saldo de Luis", "¿y cuánto debe?", "el de Monterrey"):
            with self.subTest(question=question):
                history, state = self.listed()
                self.answer(question, history, state)
                self.provider.complete.assert_called()
                self.assertNotEqual(
                    self.assistant.last_trace.get("local_decision"), "pending_clarification"
                )

    def test_relative_period_repeats_the_pending_operation(self):
        from dbagg.routing.state import ConversationState

        state = ConversationState(
            previous_route="clientes.total_pagos",
            selected_customer_id="M.ROBLES3",
            entity="cliente",
            pending_clarification="periodo",
            pending_operation="total_pagos",
        )
        history = [
            {"role": "user", "content": "¿Cuánto ha pagado Mario Robles?"},
            {
                "role": "assistant",
                "content": "Saldo actual de MARIO ROBLES (código M.ROBLES3): 300.00.",
            },
            {"role": "user", "content": "¿Y en septiembre?"},
            {"role": "assistant", "content": "¿Qué período quieres consultar?"},
        ]
        text = self.answer("este mes", history, state)
        self.provider.complete.assert_not_called()
        self.assertIn("Total de pagos activos de MARIO ROBLES (código M.ROBLES3): 40.00", text)
        self.provider.complete.return_value = reply("pedir_aclaracion", {"reason": "criterio"})
        self.answer("el año pasado", history, state)
        self.provider.complete.assert_called()


class LegacyContinuityTests(unittest.TestCase):
    """A verified legacy result keeps the customer for the next modular turn."""

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.db.payment(1, "2026-10-02", "40")
        self.provider = Mock()
        self.assistant = self.make(ModularConfig(enabled=True))

    def make(self, modular):
        return Assistant(
            SimpleNamespace(
                model="test",
                tables={CUSTOMERS, PAYMENTS},
                llm=LLMConfig(ModelProfile(), modular=modular),
            ),
            db=self.db,
            provider=self.provider,
        )

    def legacy_turn(self, assistant=None):
        assistant = assistant or self.assistant
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "legacy"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        ]
        text = assistant.answer("Saldo de Ana Demo")
        self.assertIn("Saldo actual de Ana Demo (código C1)", text)
        return [
            {"role": "user", "content": "Saldo de Ana Demo"},
            {"role": "assistant", "content": text},
        ], assistant.next_state

    def test_legacy_then_modular_keeps_the_customer(self):
        history, state = self.legacy_turn()
        self.assertEqual(state.selected_customer_id, "C1")
        self.assertEqual(state.entity, "cliente")
        self.assertEqual(state.previous_route, "clientes.saldo")
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.ultimo_pago"}),
            reply(
                "consultar_negocio",
                {"operation": "ultimo_pago", "customer": "C1", "codigo_exacto": True},
            ),
        ]
        text = self.assistant.answer("¿Y su último pago?", history, state)
        self.assertIn("Ana Demo (código C1)", text)
        system = self.provider.complete.call_args_list[-1].args[0][0]["content"]
        self.assertIn('"selected_customer_id": "C1"', system)
        self.assertEqual(self.assistant.next_state.selected_customer_id, "C1")

    def test_domain_change_clears_the_legacy_customer(self):
        history, state = self.legacy_turn()
        self.assertIsNone(state.for_route("gastos.exploracion").selected_customer_id)

    def test_unverified_legacy_answers_store_nothing(self):
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "legacy"}),
            reply("pedir_aclaracion", {"reason": "criterio"}),
        ]
        self.assistant.answer("¿Y lo otro?")
        self.assertIsNone(self.assistant.next_state.selected_customer_id)
        self.assertIsNone(self.assistant.next_state.previous_route)

    def test_without_modular_nothing_changes(self):
        assistant = self.make(ModularConfig())
        self.provider.complete.side_effect = [
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"})
        ]
        assistant.answer("Saldo de Ana Demo")
        self.assertIsNone(assistant.next_state.previous_route)

    def test_reset_command_forgets_the_state(self):
        from dbagg.services.memory import ConversationMemory

        _, state = self.legacy_turn()
        memory = ConversationMemory()
        memory.remember("one", "Saldo de Ana Demo", "texto", state=state)
        self.assertEqual(memory.state("one").selected_customer_id, "C1")
        memory.clear("one")
        self.assertIsNone(memory.state("one").selected_customer_id)


class ExtractionRetryTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()

    @staticmethod
    def recording(*replies):
        """A provider that keeps a copy of the messages of each call."""
        provider = Mock()
        provider.seen = []
        queue = list(replies)

        def complete(messages, tools, require_tool=False):
            provider.seen.append([dict(m) for m in messages])
            return queue.pop(0)

        provider.complete.side_effect = complete
        return provider

    def test_fallback_receives_the_rejected_call_and_the_reason(self):
        provider = self.recording(
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        )
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=provider,
        )
        nano = Mock()
        nano.complete.return_value = reply("consultar_negocio", {"operation": "ventas"})
        assistant.extraction_provider = nano
        self.assertIn("123.45", assistant.answer("Saldo de Ana Demo"))
        messages = provider.seen[-1]
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertEqual(messages[-2]["tool_calls"][0]["function"]["name"], "consultar_negocio")
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("Valor fuera del contrato seleccionado.", messages[-1]["content"])
        self.assertEqual(provider.complete.call_count, 2)

    def test_transport_errors_add_nothing_to_the_conversation(self):
        provider = self.recording(
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        )
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=provider,
        )
        nano = Mock()
        nano.complete.side_effect = RuntimeError("private")
        assistant.extraction_provider = nano
        assistant.answer("Saldo de Ana Demo")
        messages = provider.seen[-1]
        self.assertEqual(messages[-1]["role"], "user")

    def test_same_profile_shares_one_provider_instance(self):
        from unittest.mock import patch

        profile = ModelProfile(name="openai")
        config = LLMConfig(
            profile,
            modular=ModularConfig(enabled=True, extraction_profile="openai"),
            profiles={"openai": profile},
        )
        provider = Mock()
        with (
            patch("dbagg.agent.service.create_provider") as create,
            patch.dict("os.environ", {profile.api_key_env: "synthetic"}),
        ):
            assistant = Assistant(
                SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
                db=self.db,
                provider=provider,
            )
            create.assert_not_called()
        self.assertIs(assistant.extraction_provider, provider)
        assistant.close()
        provider.close.assert_called_once()


class TieredRoutingTests(unittest.TestCase):
    """Jev's probabilities pick an operation, a domain module or legacy, with one call."""

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.provider = Mock()
        config = LLMConfig(
            ModelProfile(),
            modular=ModularConfig(
                enabled=True,
                router="jev",
                min_confidence=0.8,
                min_margin=0.3,
                min_domain_confidence=0.7,
            ),
        )
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=self.provider,
        )
        self.assistant.router.close()
        self.assistant.router = Mock()

    def decide(self, probabilities):
        route = max(probabilities, key=probabilities.get)
        self.assistant.router.decide.return_value = RoutingDecision(
            route, "jev", probabilities[route], probabilities
        )

    def routing_calls(self):
        return self.assistant.router.decide.call_count + sum(
            1
            for c in self.provider.complete.call_args_list
            if c.args[1][0]["function"]["name"] == "select_route"
        )

    def operations_offered(self):
        tools = self.provider.complete.call_args_list[-1].args[1]
        function = next(
            t["function"] for t in tools if t["function"]["name"] == "consultar_negocio"
        )
        return function["parameters"]["properties"]["operation"]["enum"]

    def test_confident_wide_margin_selects_the_operation(self):
        self.decide({"clientes.saldo": 0.9, "clientes.pagos": 0.05, "legacy": 0.05})
        self.provider.complete.return_value = reply(
            "consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}
        )
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(self.assistant.last_trace["routing_level"], "operation")
        self.assertEqual(self.operations_offered(), ["saldo"])
        self.assertEqual(self.routing_calls(), 1)

    def test_unsure_operation_in_a_sure_domain_offers_the_domain_module(self):
        self.decide(
            {
                "clientes.total_pagos": 0.45,
                "clientes.pagos": 0.35,
                "clientes.ultimo_pago": 0.1,
                "clientes.saldo": 0.04,
                "legacy": 0.06,
            }
        )
        self.provider.complete.return_value = reply(
            "consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}
        )
        self.assistant.answer("¿Cuánto pagó Ana Demo?")
        self.assertEqual(self.assistant.last_trace["routing_level"], "domain")
        # ultimo_pago passes the floor but the consistency veto drops it: no «último» asked.
        self.assertEqual(
            self.assistant.last_trace["route_candidates"],
            ["clientes.total_pagos", "clientes.pagos"],
        )
        self.assertEqual(self.operations_offered(), ["total_pagos", "pagos"])
        self.assertEqual(self.routing_calls(), 1)

    def test_scattered_distribution_goes_to_legacy_without_the_mini_classifier(self):
        self.decide({"clientes.saldo": 0.35, "gastos.exploracion": 0.3, "legacy": 0.35})
        self.provider.complete.return_value = reply("pedir_aclaracion", {"reason": "criterio"})
        self.assistant.answer("¿Y eso?")
        self.assertEqual(self.assistant.last_trace["routing_level"], "legacy")
        self.assertEqual(self.routing_calls(), 1)
        self.assertNotIn("route", self.assistant.last_trace)

    def test_transport_error_still_uses_the_mini_classifier_once(self):
        self.assistant.router.decide.side_effect = httpx.ConnectTimeout("timeout")
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        ]
        self.assertIn("123.45", self.assistant.answer("Saldo de Ana Demo"))
        self.assertEqual(self.routing_calls(), 2)

    def test_without_tier_fields_behaviour_is_unchanged(self):
        from dbagg.agent.service import tiered_decision

        decision = RoutingDecision("clientes.saldo", "jev", 0.9, {"clientes.saldo": 0.9})
        routes = available_routes(load_catalog(), {CUSTOMERS, PAYMENTS})
        level, chosen = tiered_decision(decision, routes, ModularConfig(min_confidence=0.8))
        self.assertEqual((level, [r.name for r in chosen]), ("operation", ["clientes.saldo"]))
        self.assistant.llm_config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, router="jev", min_confidence=0.8)
        )
        self.decide({"clientes.saldo": 0.6, "clientes.pagos": 0.4})
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo", "customer": "Ana Demo"}),
        ]
        self.assistant.answer("Saldo de Ana Demo")
        self.assertNotIn("routing_level", self.assistant.last_trace)
        self.assertEqual(self.routing_calls(), 2)  # low confidence → mini, as before


class RouterInstructionTests(unittest.TestCase):
    def setUp(self):
        self.routes = available_routes(load_catalog(), frozenset(load_catalog().sources))

    def test_instructions_are_general_and_name_nobody(self):
        import re

        from dbagg.routing.service import INSTRUCTIONS

        self.assertNotIn("Enrique", INSTRUCTIONS)
        self.assertNotIn("Rojas", INSTRUCTIONS)
        # Apart from the first word of each sentence, no capitalized words (names) remain.
        words = re.findall(r"(?<![.¿¡] )(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+", INSTRUCTIONS)
        self.assertEqual(words, [])
        self.assertLess(len(INSTRUCTIONS), 600)

    def test_cases_live_in_route_descriptions(self):
        options = route_options(self.routes)
        for name in (
            "clientes.total_pagos",
            "clientes.ultimo_pago",
            "gastos.exploracion",
            "legacy",
        ):
            with self.subTest(route=name):
                self.assertIn("Usar cuando", options[name])
        self.assertIn("No usar cuando", options["clientes.total_pagos"])
        self.assertIn("gastamos", options["gastos.exploracion"])
        self.assertIn("banco", options["legacy"])

    def test_reference_sends_the_instructions_once(self):
        from dbagg.routing.service import INSTRUCTIONS

        provider = Mock()
        provider.complete.return_value = reply("select_route", {"route": "clientes.saldo"})
        ReferenceRouter(provider).decide("Saldo de Demo", [], self.routes)
        messages, tools = provider.complete.call_args.args[:2]
        self.assertIn(INSTRUCTIONS, messages[0]["content"])
        description = tools[0]["function"]["description"]
        self.assertNotIn(INSTRUCTIONS, description)
        self.assertLess(len(description), 100)

    def test_routing_state_carries_previous_route_and_pending_clarification(self):
        from dbagg.routing.service import routing_state
        from dbagg.routing.state import ConversationState

        state = ConversationState(
            previous_route="clientes.saldo",
            selected_customer_id="C1",
            pending_clarification="cliente",
        )
        data = routing_state("el tercero", [], state)
        self.assertEqual(
            data["state"], {"previous_route": "clientes.saldo", "pending_clarification": "cliente"}
        )
        self.assertNotIn("C1", json.dumps(data))
        self.assertNotIn("state", routing_state("hola", []))
        provider = Mock()
        provider.complete.return_value = reply("select_route", {"route": "legacy"})
        ReferenceRouter(provider).decide("el tercero", [], self.routes, state)
        sent = json.loads(provider.complete.call_args.args[0][1]["content"])
        self.assertEqual(sent["state"]["previous_route"], "clientes.saldo")


class OperationScopedPromptTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.tables = frozenset(self.catalog.sources)
        self.routes = available_routes(self.catalog, self.tables)

    def select(self, *names):
        routes = [self.routes[n] for n in names]
        return select_context(
            routes if len(routes) > 1 else routes[0],
            load_business_context(),
            load_prompt_bundle(),
            self.catalog,
            self.tables,
        )

    def test_modular_text_names_only_the_enabled_operations(self):
        import re

        from dbagg.business.customers import OPERATIONS

        for names in (("clientes.pagos",), ("clientes.saldo", "clientes.total_pagos")):
            with self.subTest(names=names):
                selected = self.select(*names)
                enabled = {self.routes[n].operation for n in names}
                text = selected.bundle.system
                section = text[text.index("# Clientes y proveedores") :]
                section = section[: section.index("\n# ", 1)]
                # User wording between «» is not an operation name.
                section = re.sub(r"«[^»]*»", "«»", section)
                mentioned = {
                    op
                    for op in OPERATIONS + ("movimientos",)
                    if re.search(r"\b" + op + r"\b", section)
                }
                self.assertEqual(mentioned, enabled)
        self.assertIn("[[operaciones]]", (TEMPLATES / "domains/customers.md").read_text())
        self.assertNotIn("[[operaciones]]", (TEMPLATES / "system.md").read_text())

    def test_wrong_route_reason_is_modular_only(self):
        from dbagg.agent.prompts import tool_definitions

        selected = self.select("clientes.saldo")
        ask = next(t for t in selected.tools if t["function"]["name"] == "pedir_aclaracion")
        self.assertIn(
            "otra_operacion", ask["function"]["parameters"]["properties"]["reason"]["enum"]
        )
        self.assertIn("otra_operacion", selected.bundle.system)
        legacy = next(t for t in tool_definitions() if t["function"]["name"] == "pedir_aclaracion")
        self.assertNotIn(
            "otra_operacion", legacy["function"]["parameters"]["properties"]["reason"]["enum"]
        )
        self.assertNotIn("otra_operacion", (TEMPLATES / "system.md").read_text())
        expenses = self.select("gastos.exploracion")
        self.assertNotIn("shared/wrong_route", expenses.modules)


class WrongRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.db.payment(1, "2026-10-02", "40")
        self.provider = Mock()
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True))
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=self.provider,
        )

    def interpretation_calls(self):
        return [
            c
            for c in self.provider.complete.call_args_list
            if c.args[1][0]["function"]["name"] != "select_route"
        ]

    def test_one_return_to_the_full_interpreter(self):
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("pedir_aclaracion", {"reason": "otra_operacion"}),
            reply("consultar_negocio", {"operation": "total_pagos", "customer": "Ana Demo"}),
        ]
        text = self.assistant.answer("¿Cuánto ha pagado Ana Demo?")
        self.assertIn("Total de pagos activos de Ana Demo (código C1): 40.00", text)
        self.assertNotIn("otra_operacion", text)
        self.assertEqual(self.assistant.last_trace["modular_fallback"], "wrong_route")
        calls = self.interpretation_calls()
        self.assertEqual(len(calls), 2)
        legacy_tools = {t["function"]["name"] for t in calls[1].args[1]}
        self.assertIn("consultar_sql", legacy_tools)
        operations = next(
            t for t in calls[1].args[1] if t["function"]["name"] == "consultar_negocio"
        )["function"]["parameters"]["properties"]["operation"]["enum"]
        self.assertIn("total_pagos", operations)

    def test_two_in_a_row_end_in_a_criteria_clarification(self):
        self.provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.saldo"}),
            reply("pedir_aclaracion", {"reason": "otra_operacion"}),
            reply("pedir_aclaracion", {"reason": "otra_operacion"}),
        ]
        from dbagg.agent.service import CLARIFICATIONS

        text = self.assistant.answer("¿Cuánto ha pagado Ana Demo?")
        self.assertEqual(text, CLARIFICATIONS["criterio"])
        self.assertEqual(len(self.interpretation_calls()), 2)


class StablePrefixTests(unittest.TestCase):
    """Experiment flag: operations of one domain share the system prefix."""

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.db.payment(1, "2026-10-02", "40")

    def first_messages(self, route, arguments, stable=True):
        provider = Mock()
        seen = []

        def complete(messages, tools, require_tool=False):
            seen.append(([dict(m) for m in messages], tools))
            if tools[0]["function"]["name"] == "select_route":
                return reply("select_route", {"route": route})
            return reply("consultar_negocio", arguments)

        provider.complete.side_effect = complete
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, stable_prefix=stable)
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=provider,
        )
        text = assistant.answer("Consulta de Ana Demo")
        return text, seen[-1]

    def test_prefix_is_shared_and_variable_parts_go_last(self):
        text, (saldo, saldo_tools) = self.first_messages(
            "clientes.saldo", {"operation": "saldo", "customer": "Ana Demo"}
        )
        self.assertIn("123.45", text)
        text, (paid, paid_tools) = self.first_messages(
            "clientes.total_pagos", {"operation": "total_pagos", "customer": "Ana Demo"}
        )
        self.assertIn("40.00", text)
        marker = "# Operaciones de este turno"
        prefix = saldo[0]["content"].split(marker)[0]
        self.assertEqual(prefix, paid[0]["content"].split(marker)[0])
        self.assertIn("Objetos autorizados", prefix)
        self.assertNotIn("Calendario", saldo[0]["content"])
        self.assertEqual(saldo[1]["role"], "system")
        self.assertIn("Calendario", saldo[1]["content"])
        self.assertIn("Estado conversacional confirmado", saldo[1]["content"])
        suffix = saldo[0]["content"].split(marker)[1]
        self.assertIn("saldo sirve para ambos.", suffix)
        self.assertNotIn("total_pagos", suffix)
        enum = saldo_tools[0]["function"]["parameters"]["properties"]["operation"]["enum"]
        self.assertEqual(enum, ["saldo"])

    def test_flag_off_keeps_the_previous_layout(self):
        _, (messages, _) = self.first_messages(
            "clientes.saldo", {"operation": "saldo", "customer": "Ana Demo"}, stable=False
        )
        self.assertIn("Calendario", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertNotIn("# Operaciones de este turno", messages[0]["content"])


class FixedToolSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()

    def run_turn(self, *interpretations):
        provider = Mock()
        provider.complete.side_effect = [reply("select_route", {"route": "clientes.saldo"})] + [
            reply("consultar_negocio", a) for a in interpretations
        ]
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, fixed_tool_schema=True)
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=provider,
        )
        return assistant.answer("Saldo de Ana Demo"), provider

    def test_domain_enum_is_fixed_and_the_route_operation_is_enforced(self):
        text, provider = self.run_turn(
            {"operation": "total_pagos", "customer": "Ana Demo"},
            {"operation": "saldo", "customer": "Ana Demo"},
        )
        self.assertIn("123.45", text)
        tools = provider.complete.call_args_list[1].args[1]
        enum = tools[0]["function"]["parameters"]["properties"]["operation"]["enum"]
        self.assertIn("total_pagos", enum)
        self.assertIn("saldo", enum)
        self.assertEqual(self.db.calls[0][0].count("cobranza"), 0)
        retry = provider.complete.call_args_list[2].args[0]
        tool_results = [m["content"] for m in retry if m.get("role") == "tool"]
        self.assertIn("Operación fuera de la ruta seleccionada.", tool_results[0])

    def test_config_flag_is_boolean(self):
        from dbagg.llm.config import _modular

        self.assertTrue(_modular({"fixed_tool_schema": True}, {}).fixed_tool_schema)
        with self.assertRaises(ValueError):
            _modular({"fixed_tool_schema": "sí"}, {})


class CompactHistoryTests(unittest.TestCase):
    HISTORY = [
        {"role": "user", "content": "Saldo de Luis Ficticio"},
        {"role": "assistant", "content": "Saldo actual de LUIS FICTICIO (código L1): 9.00."},
        {"role": "user", "content": "Saldo de Ana Demo"},
        {"role": "assistant", "content": "Saldo actual de Ana Demo (código C1): 123.45."},
    ]

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer()
        self.db.payment(1, "2026-10-02", "40")

    def turn(self, question, route, arguments, history, state, compact=True):
        from datetime import date
        from unittest.mock import patch

        provider = Mock()
        seen = []

        def complete(messages, tools, require_tool=False):
            seen.append([dict(m) for m in messages])
            if tools[0]["function"]["name"] == "select_route":
                return reply("select_route", {"route": route})
            return reply(*arguments)

        provider.complete.side_effect = complete
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, compact_history=compact)
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}, llm=config),
            db=self.db,
            provider=provider,
        )
        with patch("dbagg.agent.service.business_today", return_value=date(2026, 10, 8)):
            text = assistant.answer(question, history, state)
        return text, seen[-1]

    def state(self):
        from dbagg.routing.state import ConversationState

        return ConversationState(
            previous_route="clientes.saldo", selected_customer_id="C1", entity="cliente"
        )

    def test_continuations_resolve_with_the_last_exchange_and_the_state(self):
        for question, route, arguments, expected in (
            (
                "¿Y su último pago?",
                "clientes.ultimo_pago",
                {"operation": "ultimo_pago", "customer": "C1", "codigo_exacto": True},
                "Pagos de la última fecha registrada de Ana Demo",
            ),
            (
                "¿Y este mes?",
                "clientes.total_pagos",
                {
                    "operation": "total_pagos",
                    "customer": "C1",
                    "codigo_exacto": True,
                    "period": "este_mes",
                },
                "Total de pagos activos de Ana Demo (código C1): 40.00",
            ),
        ):
            with self.subTest(question=question):
                text, messages = self.turn(
                    question, route, ("consultar_negocio", arguments), self.HISTORY, self.state()
                )
                self.assertIn(expected, text)
                contents = [m["content"] for m in messages]
                self.assertNotIn("Saldo de Luis Ficticio", contents)
                self.assertIn(self.HISTORY[-1]["content"], contents)
                self.assertIn('"selected_customer_id": "C1"', messages[0]["content"])

    def test_open_list_or_flag_off_keeps_the_full_history(self):
        listed = self.HISTORY + [
            {"role": "user", "content": "Saldo de Robles"},
            {
                "role": "assistant",
                "content": "Encontré varios clientes:\n• A (código A1)\n• B (código B2)\n"
                "¿Cuál quieres consultar?",
            },
        ]
        ask = ("pedir_aclaracion", {"reason": "cliente"})
        _, messages = self.turn("¿y su saldo?", "clientes.saldo", ask, listed, self.state())
        self.assertIn("Saldo de Luis Ficticio", [m["content"] for m in messages])
        _, messages = self.turn(
            "¿y su saldo?", "clientes.saldo", ask, self.HISTORY, self.state(), compact=False
        )
        self.assertIn("Saldo de Luis Ficticio", [m["content"] for m in messages])


class BankModularDomainTests(unittest.TestCase):
    def setUp(self):
        from dbagg.business.banks import BANK_SOURCES
        from tests.unit.test_bank_queries import BankDatabase

        self.tables = {CUSTOMERS, PAYMENTS, *BANK_SOURCES}
        self.db = BankDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")

    def assistant(self, *replies, domains=("clientes", "gastos", "bancos")):
        provider = Mock()
        provider.complete.side_effect = list(replies)
        config = LLMConfig(ModelProfile(), modular=ModularConfig(enabled=True, domains=domains))
        assistant = Assistant(
            SimpleNamespace(model="test", tables=self.tables, llm=config),
            db=self.db,
            provider=provider,
        )
        return assistant, provider

    def test_routes_only_when_enabled(self):
        catalog = load_catalog()
        self.assertFalse(
            any(n.startswith("bancos.") for n in available_routes(catalog, self.tables))
        )
        routes = available_routes(catalog, self.tables, ("clientes", "bancos"))
        self.assertEqual(
            {n for n in routes if n.startswith("bancos.")},
            {"bancos.saldo_banco", "bancos.saldos_bancos", "bancos.movimientos_banco"},
        )

    def test_bank_question_uses_its_route_instead_of_the_legacy_veto(self):
        assistant, provider = self.assistant(
            reply("select_route", {"route": "bancos.saldo_banco"}),
            reply("consultar_negocio", {"operation": "saldo_banco", "banco": "ENRIQUE"}),
        )
        text = assistant.answer("El banco de Enrique")
        self.assertIn("Saldo actual: -20.00", text)
        self.assertEqual(assistant.last_trace["route"], "bancos.saldo_banco")
        self.assertNotIn("local_decision", assistant.last_trace)
        tools = provider.complete.call_args_list[1].args[1]
        properties = tools[0]["function"]["parameters"]["properties"]
        self.assertEqual(properties["operation"]["enum"], ["saldo_banco"])
        self.assertIn("banco", properties)
        self.assertNotIn("customer", properties)

    def test_customer_route_is_still_vetoed_for_bank_words(self):
        assistant, provider = self.assistant(
            reply("select_route", {"route": "clientes.saldo"}),
            reply("consultar_negocio", {"operation": "saldo_banco", "banco": "ENRIQUE"}),
        )
        text = assistant.answer("El banco de Enrique")
        self.assertIn("Saldo actual: -20.00", text)
        self.assertEqual(assistant.last_trace["modular_fallback"], "route_inconsistent")

    def test_report_followup_stays_local(self):
        assistant, provider = self.assistant()
        history = [
            {"role": "user", "content": "El banco de Enrique"},
            {
                "role": "assistant",
                "content": "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\nSaldo actual: -20.00.",
            },
        ]
        assistant.answer("Dame el reporte de este mes", history)
        provider.complete.assert_not_called()
        self.assertEqual(assistant.last_trace["local_decision"], "bank_report_followup")

    def test_without_the_domain_bank_words_skip_routing(self):
        from dbagg.routing.policy import pre_route

        self.assertEqual(pre_route("El banco de Enrique"), "legacy")
        self.assertIsNone(pre_route("El banco de Enrique", bank_domain=True))
        self.assertEqual(pre_route("ok", bank_domain=True), "legacy")


class ProductModularDomainTests(unittest.TestCase):
    def test_only_stock_and_sales_get_routes(self):
        catalog = load_catalog()
        tables = frozenset(catalog.sources)
        self.assertFalse(any(n.startswith("productos.") for n in available_routes(catalog, tables)))
        routes = available_routes(catalog, tables, ("clientes", "productos"))
        self.assertEqual(
            {n for n in routes if n.startswith("productos.")},
            {"productos.existencias", "productos.ventas"},
        )
        selected = select_context(
            routes["productos.ventas"],
            load_business_context(),
            load_prompt_bundle(),
            catalog,
            tables,
        )
        properties = selected.tools[0]["function"]["parameters"]["properties"]
        self.assertEqual(properties["operation"]["enum"], ["ventas"])
        self.assertIn("producto", properties)
        self.assertIn("comparar", properties)
        self.assertNotIn("customer", properties)
        self.assertIn("domains/products", selected.modules)

    def test_sales_to_a_customer_are_not_a_product_route(self):
        from dbagg.routing.policy import route_is_consistent

        catalog = load_catalog()
        routes = available_routes(catalog, frozenset(catalog.sources), ("productos",))
        self.assertFalse(
            route_is_consistent("¿Cuánto le vendimos a Ana Demo?", routes["productos.ventas"])
        )
        self.assertTrue(
            route_is_consistent("¿Cómo van las ventas este mes?", routes["productos.ventas"])
        )

    def test_stock_question_runs_through_its_route(self):
        from tests.unit.test_product_queries import SyntheticDatabase as ProductDatabase

        db = ProductDatabase()
        self.addCleanup(db.conn.close)
        from dbagg.business.products import CASH, CREDITS as SALES, RETURNS, STOCK

        db.conn.execute(f"INSERT INTO {STOCK} VALUES ('MAIZ', 'MAIZ BLANCO', 2000)")
        provider = Mock()
        provider.complete.side_effect = [
            reply("select_route", {"route": "productos.existencias"}),
            reply("consultar_negocio", {"operation": "existencias", "producto": "maíz"}),
        ]
        config = LLMConfig(
            ModelProfile(), modular=ModularConfig(enabled=True, domains=("productos",))
        )
        assistant = Assistant(
            SimpleNamespace(model="test", tables={STOCK, SALES, CASH, RETURNS}, llm=config),
            db=db,
            provider=provider,
        )
        text = assistant.answer("¿Cuánto maíz tenemos?")
        self.assertEqual(assistant.last_trace["route"], "productos.existencias")
        self.assertIn("MAIZ", text.upper())


class RangeToTodayGateTests(unittest.TestCase):
    def test_modular_period_gate_lets_a_range_ending_today_reach_the_model(self):
        from datetime import date
        from unittest.mock import patch

        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer()
        db.payment(1, "2026-09-02", "40")
        provider = Mock()
        provider.complete.side_effect = [
            reply("select_route", {"route": "clientes.total_pagos"}),
            reply(
                "consultar_negocio",
                {
                    "operation": "total_pagos",
                    "customer": "Ana Demo",
                    "period": "rango",
                    "start": "2026-08-30",
                    "end": "2026-10-08",
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
            text = assistant.answer("¿Cuánto pagó Ana Demo del 30 de agosto a hoy?")
            self.assertIn("Total de pagos activos de Ana Demo (código C1): 40.00", text)
            provider.complete.side_effect = [
                reply("select_route", {"route": "clientes.total_pagos"})
            ]
            text = assistant.answer("¿Cuánto pagó Ana Demo en septiembre?")
        self.assertIn("¿Qué período", text)  # a month without a year is still asked
