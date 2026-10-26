import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from dbagg.agent.intent import requests_ranking
from dbagg.agent.service import Assistant
from dbagg.business.customers import CustomerQueries
from dbagg.business.evidence import BusinessError
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, TODAY, SyntheticDatabase


class MatchingBalanceTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = CustomerQueries(self.db, {CUSTOMERS, PAYMENTS}, today=TODAY)

    def ask(self, customer="Ana Robles"):
        return self.queries.execute("saldos_coincidencias", customer=customer)

    def test_each_homonym_balance_is_listed_without_a_sum(self):
        self.db.customer("R1", "Ana Robles", "0")
        self.db.customer("R2", "Ana Robles", "150.50")
        self.db.customer("R3", "Ana Robles", "-20")
        self.db.customer("Z9", "Otra Persona", "9999")
        answer = self.ask()
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.operation, "saldos_coincidencias")
        for line in ("(código R1): 0.00", "(código R2): 150.50", "(código R3): -20.00"):
            self.assertIn(line, answer.text)
        self.assertIn("no se suman", answer.text)
        self.assertNotIn("130.50", answer.text)
        self.assertNotIn("9,999", answer.text)
        self.assertEqual(self.db.calls[0][1], ("Ana Robles", "Ana Robles"))

    def test_partial_name_uses_literal_search(self):
        self.db.customer("R1", "Ana Robles Uno", "10")
        self.db.customer("R2", "Ana Robles Dos", "20")
        answer = self.ask("Robles")
        self.assertIn("R1", answer.text)
        self.assertIn("R2", answer.text)
        self.assertEqual(self.ask("%").status, "needs_clarification")

    def test_more_than_ten_matches_is_partial(self):
        for i in range(12):
            self.db.customer(f"R{i:02d}", "Ana Robles", "1")
        answer = self.ask()
        self.assertEqual(answer.status, "partial")
        self.assertIn("hay más", answer.text)
        self.assertEqual(answer.text.count("(código R"), 10)

    def test_unknown_balance_is_reported_not_zero(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.db.customer("R2", "Ana Robles", None)
        answer = self.ask()
        self.assertEqual(answer.status, "partial")
        self.assertIn("(código R2): saldo desconocido", answer.text)

    def test_duplicate_codes_are_rejected(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.db.customer("R1", "Ana Robles", "7")
        with self.assertRaisesRegex(BusinessError, "repite"):
            self.ask()

    def test_dates_are_not_accepted(self):
        self.db.customer("R1", "Ana Robles", "5")
        with self.assertRaises(BusinessError):
            self.queries.execute("saldos_coincidencias", customer="Ana Robles", period="este_mes")


class RankingGuardTests(unittest.TestCase):
    def completion(self, name, arguments):
        call = SimpleNamespace(
            id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(content=None, tool_calls=[call]),
                )
            ]
        )

    def test_ranking_needs_an_explicit_request(self):
        for question in (
            "¿Quiénes son los cinco clientes que más deben?",
            "Dame el top 5 de deudores",
            "Los 3 clientes con mayor saldo",
        ):
            with self.subTest(question=question):
                self.assertTrue(requests_ranking(question))
        for question in (
            "Revisa los saldos de los 3 Ana Robles",
            "De Ana Robles",
            "El saldo de Robles",
        ):
            with self.subTest(question=question):
                self.assertFalse(requests_ranking(question))

    def test_named_customers_never_receive_the_global_ranking(self):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        for code, balance in (("R1", "0"), ("R2", "15"), ("R3", "30")):
            db.customer(code, "Ana Robles", balance)
        db.customer("TOP1", "Gran Deudor", "900000")
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=db, client=Mock()
        )
        create = assistant.client.chat.completions.create
        create.side_effect = [
            self.completion("consultar_negocio", {"operation": "ranking_deuda", "limit": 3}),
            self.completion(
                "consultar_negocio",
                {"operation": "saldos_coincidencias", "customer": "Ana Robles"},
            ),
        ]
        history = [{"role": "user", "content": "El saldo de Ana Robles"}]
        answer = assistant.answer("Revisa los saldos de los 3 Ana Robles", history)
        self.assertNotIn("Gran Deudor", answer)
        self.assertIn("(código R3): 30.00", answer)
        self.assertEqual(assistant.last_trace["operation"], "saldos_coincidencias")
        hint = create.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(hint["role"], "tool")
        self.assertIn("saldos_coincidencias", hint["content"])


class CustomerNotFoundRetryTests(unittest.TestCase):
    completion = RankingGuardTests.completion

    def test_unknown_customer_gets_one_reroute_hint(self):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("R1", "Ana Robles", "10")
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=db, client=Mock()
        )
        create = assistant.client.chat.completions.create
        saldo = self.completion(
            "consultar_negocio", {"operation": "saldo", "customer": "Plaza Norte"}
        )
        create.side_effect = [saldo, saldo]
        answer = assistant.answer("¿Cuál es el saldo de Plaza Norte?")
        hint = create.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(hint["role"], "tool")
        self.assertIn("apartado o sucursal de gastos", hint["content"])
        self.assertIn("No encontré un cliente", answer)
        self.assertEqual(create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
