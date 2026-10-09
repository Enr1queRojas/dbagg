import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from dbagg.agent.service import Assistant


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
            self.completion("Saldo: 100"),
        ]
        self.assertEqual(self.assistant.answer("saldo total"), "Saldo: 100")
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
            "¿Qué cliente?"
        )
        self.assertEqual(self.assistant.answer("su saldo"), "¿Qué cliente?")
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
        self.assistant.client.chat.completions.create.side_effect = [
            self.describe(),
            self.query(
                "SELECT CLIENTE FROM proadel.vw_AlertasCobranza WHERE NOMBRE LIKE '%Esparza%'"
            ),
            self.query(
                "SELECT SALDO_PENDIENTE FROM proadel.vw_AlertasCobranza WHERE CLIENTE='A.ESPARZA3'"
            ),
            self.completion("El saldo consultado es 100."),
        ]
        self.assertEqual(
            self.assistant.answer("¿Cuánto debe Esparza?"), "El saldo consultado es 100."
        )
        self.assertEqual(self.assistant.db.query.call_count, 2)
        last_messages = self.assistant.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertTrue(any(m["role"] == "tool" for m in last_messages))

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
