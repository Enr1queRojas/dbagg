import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dbagg.agent.service import Assistant
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.assistant = Assistant.__new__(Assistant)
        self.assistant.settings = SimpleNamespace(
            model="test", tables={"proadel.vw_alertascobranza"}
        )
        self.assistant.db = Mock()
        self.assistant.db.schema.return_value = '[{"table":"proadel.vw_alertascobranza"}]'
        self.assistant.db.query.return_value = {"columns": ["saldo"], "rows": [["100"]]}
        self.assistant.client = Mock()

    def completion(self, content=None, name=None, arguments=None):
        calls = (
            []
            if name is None
            else [
                SimpleNamespace(
                    id="call1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
                )
            ]
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))]
        )

    def describe(self):
        return self.completion(
            name="describir_tablas", arguments={"tables": ["proadel.vw_alertascobranza"]}
        )

    def query(self, sql):
        return self.completion(name="consultar_sql", arguments={"sql": sql})

    def test_validated_query_then_answer(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.describe(),
            self.query("SELECT SUM(SALDO_PENDIENTE) AS saldo FROM proadel.vw_AlertasCobranza"),
            self.completion("Saldo: 999"),
        ]
        answer = self.assistant.answer("saldo total")
        self.assertIn("saldo: 100", answer)
        self.assertNotIn("999", answer)
        self.assertIn("requiere revisión", answer)
        self.assertIn("TOP 50", self.assistant.db.query.call_args.args[0])
        self.assertEqual(self.assistant.client.chat.completions.create.call_count, 3)

    def test_model_generated_write_never_reaches_database(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.query("DELETE FROM proadel.vw_AlertasCobranza"),
            self.completion("No puedo modificar datos."),
        ]
        self.assistant.answer("elimina todos los clientes")
        self.assistant.db.query.assert_not_called()

    def test_clarification_does_not_query_database(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            name="pedir_aclaracion", arguments={"reason": "cliente"}
        )
        self.assertEqual(
            self.assistant.answer("su saldo"),
            "¿De qué cliente quieres consultar el saldo o los pagos? Indica su nombre o código.",
        )
        self.assistant.db.query.assert_not_called()

    def test_business_definitions_reach_model_without_changing_permissions(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            "¿Ventas netas?"
        )
        context = '{"topics":{"sales":{"status":"pending","definition":"criterio_pendiente"}}}'
        with patch("dbagg.agent.service.load_business_context", return_value=context):
            self.assistant.answer("¿Cuánto vendimos?")
        prompt = self.assistant.client.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ]
        self.assertIn(context, prompt)
        self.assertEqual(self.assistant.settings.tables, {"proadel.vw_alertascobranza"})
        self.assistant.db.query.assert_not_called()

    def test_followup_history_is_sent_to_model_without_mutation(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            "Voy a consultar ventas."
        )
        history = [
            {"role": "user", "content": "¿Cuánto vendimos?"},
            {"role": "assistant", "content": "¿De esta semana?"},
        ]
        self.assistant.answer("sí", history=history)
        messages = self.assistant.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[1:3], history)
        self.assertEqual(messages[-1]["content"], "sí")
        self.assertEqual(len(history), 2)

    def test_natural_name_can_be_resolved_before_balance(self):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Prueba", "100")
        self.assistant.db = db
        self.assistant.settings.tables = {CUSTOMERS, PAYMENTS}
        self.assistant.client.chat.completions.create.return_value = self.completion(
            "Debe 999 pesos",
            name="consultar_negocio",
            arguments={"operation": "saldo", "customer": "Prueba"},
        )
        answer = self.assistant.answer("¿Cuánto debe Prueba?")
        self.assertEqual(
            answer,
            "Saldo actual de Cliente Prueba (código DEMO1): 100.00.\nImporte sin moneda confirmada.",
        )
        self.assertEqual(len(db.calls), 3)
        self.assertEqual(self.assistant.last_trace["operation"], "saldo")
        self.assertTrue(self.assistant.last_trace["evidence_id"])
        self.assertEqual(self.assistant.client.chat.completions.create.call_count, 1)

    def test_model_cannot_answer_financial_facts_without_a_query(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            "Debe 999 pesos"
        )
        answer = self.assistant.answer("¿Cuánto debe Prueba?")
        self.assertIn("No obtuve resultados", answer)
        self.assertNotIn("999", answer)
        self.assertEqual(self.assistant.last_trace["status"], "needs_clarification")

    def test_failed_query_invalidates_previous_exploratory_result(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.describe(),
            self.query("SELECT SALDO_PENDIENTE FROM proadel.vw_AlertasCobranza"),
            self.query("SELECT dato FROM otra.secreta"),
            self.completion("Confirmado: 100"),
        ]
        answer = self.assistant.answer("consulta")
        self.assertNotIn("100", answer)
        self.assertIn("No obtuve resultados", answer)
        self.assertEqual(self.assistant.last_trace["sources"], [])

    def test_protected_financial_sources_cannot_bypass_business_tools(self):
        self.assistant.settings.tables = {CUSTOMERS}
        self.assistant.client.chat.completions.create.side_effect = [
            self.completion(name="describir_tablas", arguments={"tables": [CUSTOMERS]}),
            self.query(f"SELECT SUM([TOTAL_CREDITO]) FROM {CUSTOMERS}"),
            self.completion("Su deuda es 999"),
        ]
        answer = self.assistant.answer("saldo")
        self.assistant.db.query.assert_not_called()
        self.assertNotIn("999", answer)

    def test_invented_customer_is_rejected_before_schema_or_query(self):
        self.assistant.client.chat.completions.create.return_value = self.completion(
            name="consultar_negocio", arguments={"operation": "saldo", "customer": "INVENTADO3"}
        )
        self.assertIn("No pude vincular", self.assistant.answer("¿Cuánto debe Prueba?"))
        self.assistant.db.schema.assert_not_called()
        self.assistant.db.query.assert_not_called()

    def test_followup_can_use_customer_selected_in_conversation(self):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("DEMO1", "Cliente Prueba", 100)
        db.payment(1, "2023-01-25", 20, customer="DEMO1")
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=db, client=Mock()
        )
        assistant.client.chat.completions.create.return_value = self.completion(
            name="consultar_negocio", arguments={"operation": "ultimo_pago", "customer": "DEMO1"}
        )
        history = [
            {
                "role": "assistant",
                "content": "Saldo actual de Cliente Prueba (código DEMO1): 100.00.",
            }
        ]
        answer = assistant.answer("¿Y su último pago?", history)
        self.assertIn("2023-01-25", answer)
        self.assertIn("20.00", answer)

    def test_calendar_passed_to_model_uses_business_timezone(self):
        from datetime import date

        self.assistant.settings.business_timezone = "America/Mexico_City"
        self.assistant.client.chat.completions.create.return_value = self.completion(
            name="pedir_aclaracion", arguments={"reason": "criterio"}
        )
        with patch("dbagg.agent.service.business_today", return_value=date(2026, 10, 8)) as clock:
            self.assistant.answer("esta semana")
        clock.assert_called_once_with("America/Mexico_City")
        prompt = self.assistant.client.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ]
        self.assertIn('"today": "2026-10-08"', prompt)
        self.assertIn('"week_start": "2026-10-05"', prompt)

    def test_query_requires_inspecting_real_columns(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.query("SELECT CLIENTE FROM proadel.vw_AlertasCobranza"),
            self.completion("Necesito inspeccionar."),
        ]
        self.assistant.answer("clientes")
        self.assistant.db.query.assert_not_called()

    def test_unknown_table_cannot_be_described(self):
        self.assistant.client.chat.completions.create.side_effect = [
            self.completion(name="describir_tablas", arguments={"tables": ["otra.secreta"]}),
            self.completion("Objeto no autorizado."),
        ]
        self.assistant.answer("consulta otra tabla")
        self.assistant.db.schema.assert_not_called()

    def test_tool_loop_is_bounded(self):
        self.assistant.client.chat.completions.create.return_value = self.describe()
        self.assistant.answer("consulta sin terminar")
        self.assertEqual(self.assistant.db.schema.call_count, 4)
        self.assertEqual(self.assistant.client.chat.completions.create.call_count, 9)
