"""The service, not the model, decides between customer and supplier."""

import json
import unittest
from types import SimpleNamespace

import pyodbc

from dbagg.agent.prompts import tool_definitions
from dbagg.agent.service import Assistant
from dbagg.business.customers import CREDITS, CUSTOMERS, PAYMENTS
from dbagg.business.suppliers import PURCHASES, SUPPLIER_PAYMENTS, SUPPLIERS
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.types import ModelReply, ToolCall
from tests.unit.test_customer_queries import CREDIT_COLUMNS
from tests.unit.test_supplier_queries import DEFINITIONS, SupplierDatabase

CUSTOMER_DEFINITIONS = {
    CUSTOMERS: {"CODIGO": "nvarchar", "NOMBRE": "nvarchar", "SALDO ACTUAL": "decimal"},
    PAYMENTS: {
        "CLIENTE": "nvarchar",
        "ESTADO": "nvarchar",
        "FECHA": "date",
        "IMPORTE": "decimal",
        "ID": "bigint",
        "FOLIO": "nvarchar",
    },
}
ROBLES_TEXT = "Saldo actual de Robles (código R1): 5.00.\nImporte sin moneda confirmada."


class RoutingDatabase(SupplierDatabase):
    def __init__(self, check_same_thread=True):
        super().__init__(check_same_thread)
        self.conn.execute(f'CREATE TABLE {CUSTOMERS} (CODIGO, NOMBRE, "SALDO ACTUAL" NUMERIC)')
        self.conn.execute(f"CREATE TABLE {PAYMENTS} (ID, FOLIO, CLIENTE, FECHA, IMPORTE, ESTADO)")
        self.statements = []
        self.customer("R1", "Robles", 5)
        self.customer("A1", "Ana", 11)
        self.customer("X", "Otra Persona", 12)
        self.supplier("P1", "Ana", "22")
        self.supplier("X", "Ana Dos", "33")
        self.supplier("C1", "Cuervo", "44")

    def customer(self, code, name, balance):
        self.conn.execute(f"INSERT INTO {CUSTOMERS} VALUES (?, ?, ?)", (code, name, balance))

    def schema(self, sources):
        definitions = {**DEFINITIONS, **CUSTOMER_DEFINITIONS}
        return json.dumps(
            [
                {
                    "table": table,
                    "columns": [{"name": n, "type": t} for n, t in definitions[table].items()],
                }
                for table in sources
            ]
        )

    def query(self, sql, params=(), row_limit=50):
        self.statements.append(sql)
        return super().query(sql, params, row_limit)


class OneCallProvider:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, tools, require_tool=False):
        return self.reply

    def close(self):
        pass


def assistant_message(content):
    return {"role": "assistant", "content": content}


class RoutingCase(unittest.TestCase):
    TABLES = {CUSTOMERS, PAYMENTS, SUPPLIERS, SUPPLIER_PAYMENTS, PURCHASES}

    def setUp(self):
        self.db = RoutingDatabase()
        self.addCleanup(self.db.conn.close)

    def ask(self, operation, question="¿Cuánto debe?", history=None, tables=None, **arguments):
        call = ToolCall(
            "t1", "consultar_negocio", json.dumps({"operation": operation, **arguments})
        )
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables=tables or self.TABLES,
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        provider = OneCallProvider(ModelReply(None, (call,), {}))
        service = Assistant(settings, db=self.db, provider=provider)
        named = arguments.get("customer")
        if named and named not in question and not history:
            question = f"{question} {named}"
        return service.answer(question, history)


class SupplierRoutingTests(RoutingCase):
    def test_only_supplier_routes_to_supplier(self):
        self.assertIn("Proveedor Cuervo", self.ask("saldo", customer="Cuervo"))

    def test_only_customer_routes_to_customer(self):
        self.assertEqual(self.ask("saldo", customer="Robles"), ROBLES_TEXT)

    def test_name_in_both_asks_which(self):
        text = self.ask("saldo", customer="Ana")
        self.assertIn("como cliente y como proveedor", text)
        self.assertIn("(cliente, código A1)", text)
        self.assertIn("(proveedor, código P1)", text)

    def test_explicit_word_skips_the_question(self):
        text = self.ask("saldo", customer="P1", question="¿Cuánto le debemos a P1?")
        self.assertTrue(text.startswith("Proveedor Ana"))

    def test_model_entity_narrows_a_name_in_both(self):
        text = self.ask("saldo", customer="Ana", entidad="cliente", question="saldo de Ana")
        self.assertIn("Saldo actual de Ana", text)

    def test_code_in_both_catalogs_keeps_conversation_entity(self):
        self.db.payment(1, "X", "2026-01-05", "7")
        history = [assistant_message("Proveedor Ana Dos (código X)\nLe debemos: 33.00.")]
        text = self.ask(
            "pagos", customer="X", codigo_exacto=True, question="¿y sus pagos?", history=history
        )
        self.assertIn("Pagos que le hicimos", text)

    def test_explicit_word_overrides_conversation(self):
        history = [assistant_message("Proveedor Robles (código R1)\nLe debemos: 1.00.")]
        text = self.ask("saldo", customer="Robles", question="¿y como cliente?", history=history)
        self.assertEqual(text, ROBLES_TEXT)

    def test_mixed_list_position_uses_row_entity(self):
        history = [
            assistant_message(
                "Encontré «Ana» como cliente y como proveedor:\n"
                "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n"
                "¿Cuál quieres consultar?"
            )
        ]
        text = self.ask("saldo", customer="Ana", question="el segundo", history=history)
        self.assertTrue(text.startswith("Proveedor Ana (código P1)"))

    def test_entity_word_answers_a_mixed_list(self):
        history = [
            assistant_message(
                "Encontré «Ana» como cliente y como proveedor:\n"
                "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n"
                "¿Cuál quieres consultar?"
            )
        ]
        text = self.ask("saldo", customer="Ana", question="el proveedor", history=history)
        self.assertTrue(text.startswith("Proveedor Ana (código P1)"))
        text = self.ask("saldo", customer="Ana", question="el cliente", history=history)
        self.assertIn("Saldo actual de Ana (código A1)", text)

    def test_explicit_supplier_missing_but_customer_exists_says_so(self):
        text = self.ask("saldo", customer="Robles", question="saldo del proveedor Robles")
        self.assertIn("Robles sí aparece como cliente", text)

    def test_supplier_only_operation_on_customer_is_clear(self):
        text = self.ask("movimientos", customer="Robles")
        self.assertIn("todavía solo está disponible para proveedores", text)

    def test_last_note_of_customer_uses_credit_notes(self):
        self.db.conn.execute(
            f"CREATE TABLE {CREDITS} (CLIENTE, ESTADO, FECHA, FOLIO, CONCEPTO, CANTIDAD, "
            "PRECIO, TOTAL NUMERIC)"
        )
        self.db.conn.execute(
            f"INSERT INTO {CREDITS} VALUES ('R1', 'ACTIVA', '2026-10-01', 7, 'Maíz', 1, 1, 80)"
        )
        CUSTOMER_DEFINITIONS[CREDITS] = CREDIT_COLUMNS
        self.addCleanup(CUSTOMER_DEFINITIONS.pop, CREDITS)
        text = self.ask(
            "ultima_nota",
            customer="Robles",
            question="¿Cuál fue la última compra de Robles?",
            tables=self.TABLES | {CREDITS},
        )
        self.assertIn("Última nota de crédito de Robles (código R1)", text)
        self.assertIn("Importe de la nota: 80.00.", text)
        self.assertNotIn("solo está disponible para proveedores", text)
        self.assertFalse(any(PURCHASES in sql for sql in self.db.statements))

    def test_last_note_of_supplier_still_uses_purchases(self):
        self.db.purchase(1, 5, "C1", "2026-10-01", 30, total=999)
        text = self.ask("ultima_nota", customer="Cuervo", question="la última nota de Cuervo")
        self.assertTrue(text.startswith("Proveedor Cuervo"))
        self.assertIn("Importe de la nota: 30.00", text)
        self.assertNotIn("999", text)

    MIXED = (
        "Encontré «Ana» como cliente y como proveedor:\n"
        "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n"
        "• Ana Dos (proveedor, código X)\n¿Cuál quieres consultar?"
    )

    def test_topic_change_after_mixed_list_answers_the_named_party(self):
        history = [assistant_message(self.MIXED)]
        text = self.ask(
            "saldo", customer="Cuervo", question="¿Cuánto le debemos a Cuervo?", history=history
        )
        self.assertTrue(text.startswith("Proveedor Cuervo"))
        text = self.ask(
            "saldo", customer="Robles", question="¿Cuánto nos debe Robles?", history=history
        )
        self.assertEqual(text, ROBLES_TEXT)

    def test_denied_supplier_access_keeps_customer_questions_working(self):
        db = self.db
        original = db.query

        def denied(sql, params=(), row_limit=50):
            if SUPPLIERS in sql:
                raise pyodbc.ProgrammingError("42000", "denied")
            return original(sql, params, row_limit)

        db.query = denied
        self.assertEqual(self.ask("saldo", customer="Robles"), ROBLES_TEXT)

    def test_exploratory_answer_sets_no_entity(self):
        history = [assistant_message("PROVEEDOR | IMPORTE\nX | 100.00\nLista completa.")]
        text = self.ask("saldo", customer="X", question="¿cuál es el saldo de X?", history=history)
        self.assertIn("como cliente y como proveedor", text)

    def test_found_as_other_entity_sets_the_context(self):
        history = [
            assistant_message(
                "No encontré a Cuervo como cliente. Cuervo sí aparece como proveedor."
            )
        ]
        text = self.ask("saldo", customer="Cuervo", question="¿y su saldo?", history=history)
        self.assertTrue(text.startswith("Proveedor Cuervo"))

    def test_an_amount_in_the_answer_is_not_a_party(self):
        self.db.customer("100", "Cliente Cien", 9)
        self.db.supplier("X100", "Beta", "1")
        history = [assistant_message("Proveedor Beta (código C7)\nLe debemos: 100.00.")]
        text = self.ask("saldo", customer="100", question="¿cuánto debe el 100?", history=history)
        self.assertIn("como cliente y como proveedor", text)

    def test_partial_name_does_not_inherit_the_entity(self):
        history = [assistant_message("Proveedor Ana Dos (código X)\nLe debemos: 33.00.")]
        text = self.ask("saldo", customer="Ana", question="¿cuánto debe Ana?", history=history)
        self.assertIn("como cliente y como proveedor", text)

    def test_ordinal_with_entity_counts_within_that_entity(self):
        history = [assistant_message(self.MIXED)]
        text = self.ask("saldo", customer="Ana", question="el segundo proveedor", history=history)
        self.assertTrue(text.startswith("Proveedor Ana Dos (código X)"))

    def test_entity_word_with_several_rows_lists_only_that_entity(self):
        history = [assistant_message(self.MIXED)]
        text = self.ask("saldo", customer="Ana", question="el proveedor", history=history)
        self.assertTrue(text.startswith("Encontré varios proveedores:"))
        self.assertNotIn("A1", text)

    def test_verb_against_context_asks_when_code_is_in_both(self):
        history = [assistant_message("Proveedor Ana Dos (código X)\nSaldo a nuestro favor: 33.00.")]
        text = self.ask(
            "saldo",
            customer="X",
            codigo_exacto=True,
            question="¿y cuánto nos debe?",
            history=history,
        )
        self.assertIn("como cliente y como proveedor", text)

    def test_name_containing_entity_word_does_not_decide(self):
        self.db.customer("PA", "Proveedores Agricolas", 3)
        text = self.ask(
            "saldo",
            customer="Proveedores Agricolas",
            question="¿cuánto debe Proveedores Agricolas?",
        )
        self.assertIn("Saldo actual de Proveedores Agricolas", text)

    def test_without_supplier_sources_supplier_question_is_not_answered_as_customer(self):
        text = self.ask(
            "saldo",
            customer="Robles",
            question="¿Cuánto le debemos a Robles?",
            tables={CUSTOMERS, PAYMENTS},
        )
        self.assertIn("no está habilitada", text)

    def test_naming_a_row_answers_the_list_even_if_the_model_sends_its_code(self):
        history = [
            assistant_message(
                "Encontré varios proveedores:\n• Cuervo (código C1)\n• Ana (código P1)\n¿Cuál quieres consultar?"
            )
        ]
        text = self.ask("saldo", customer="C1", question="Cuervo", history=history)
        self.assertTrue(text.startswith("Proveedor Cuervo (código C1)"))

    def test_ranking_uses_explicit_supplier(self):
        text = self.ask("ranking_deuda", question="¿A quién le debemos más?")
        self.assertIn("Proveedores a los que más les debemos", text)

    def test_unnamed_ranking_stays_with_customers(self):
        text = self.ask("ranking_deuda", question="¿Quiénes deben más?")
        self.assertIn("Clientes con mayor saldo", text)

    def test_without_supplier_sources_customers_are_unchanged(self):
        text = self.ask("saldo", customer="Robles", tables={CUSTOMERS, PAYMENTS})
        self.assertEqual(text, ROBLES_TEXT)
        self.assertFalse(any(SUPPLIERS in sql for sql in self.db.statements))

    def test_unknown_name_gets_one_retry_then_a_clear_answer(self):
        text = self.ask("saldo", customer="Nadie")
        self.assertIn("No encontré", text)


class ToolDefinitionTests(unittest.TestCase):
    def properties(self):
        for tool in tool_definitions():
            function = tool.get("function", tool)
            if function["name"] == "consultar_negocio":
                return function, function["parameters"]["properties"]
        raise AssertionError("Falta consultar_negocio")

    def test_tool_offers_supplier_operations_and_entity(self):
        _, props = self.properties()
        self.assertIn("movimientos", props["operation"]["enum"])
        self.assertIn("ultima_nota", props["operation"]["enum"])
        self.assertEqual(props["entidad"]["enum"], ["cliente", "proveedor"])

    def test_without_suppliers_the_tool_offers_nothing_of_theirs(self):
        tools = tool_definitions(suppliers=False)
        function = next(
            t.get("function", t)
            for t in tools
            if t.get("function", t)["name"] == "consultar_negocio"
        )
        props = function["parameters"]["properties"]
        self.assertNotIn("movimientos", props["operation"]["enum"])
        self.assertNotIn("entidad", props)

    def test_tool_description_requires_no_table_names(self):
        function, _ = self.properties()
        self.assertNotIn("PAGO_PROVEEDOR", function["description"])
