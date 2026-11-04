"""The service, not the model, decides between customer and supplier."""

import json
import unittest
from types import SimpleNamespace

from dbagg.agent.prompts import tool_definitions
from dbagg.agent.service import Assistant
from dbagg.business.customers import CUSTOMERS, PAYMENTS
from dbagg.business.suppliers import PURCHASES, SUPPLIER_PAYMENTS, SUPPLIERS
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.types import ModelReply, ToolCall
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

    def test_tool_description_requires_no_table_names(self):
        function, _ = self.properties()
        self.assertNotIn("PAGO_PROVEEDOR", function["description"])
