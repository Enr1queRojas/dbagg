import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from dbagg.agent.intent import requests_ranking
from dbagg.agent.prompts import tool_definitions
from dbagg.agent.service import Assistant
from dbagg.business.customers import MATCHES_NOT_FOUND, CustomerQueries
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
        self.assertEqual(
            self.db.calls[0][1], ("%Ana Robles%", "%Ana Robles%", "Ana Robles", "Ana Robles")
        )

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


class CodeAlsoMatchingNamesTests(unittest.TestCase):
    """A customer's code can be the surname of other customers: "SEGURA" / DIEGO SEGURA."""

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = CustomerQueries(self.db, {CUSTOMERS, PAYMENTS}, today=TODAY)
        self.db.customer("SEGURA", "TORTILLERIA SEGURA", "0")
        self.db.customer("DIEGO", "DIEGO SEGURA", "2194991.60")
        self.db.customer("OTRO", "Otra Persona", "9999")

    def test_matching_balances_include_names_beside_the_exact_code(self):
        answer = self.queries.execute("saldos_coincidencias", customer="SEGURA")
        self.assertIn("TORTILLERIA SEGURA (código SEGURA): 0.00", answer.text)
        self.assertIn("DIEGO SEGURA (código DIEGO): 2,194,991.60", answer.text)
        self.assertNotIn("Otra Persona", answer.text)

    def test_balance_asks_which_customer_without_showing_amounts(self):
        answer = self.queries.execute("saldo", customer="SEGURA")
        self.assertEqual(answer.status, "needs_clarification")
        self.assertIn("TORTILLERIA SEGURA (código SEGURA)", answer.text)
        self.assertIn("DIEGO SEGURA (código DIEGO)", answer.text)
        self.assertNotIn("2,194,991.60", answer.text)

    def test_confirmed_code_selects_only_that_customer(self):
        answer = self.queries.execute("saldo", customer="SEGURA", codigo_exacto=True)
        self.assertEqual(answer.status, "answered")
        self.assertIn("TORTILLERIA SEGURA (código SEGURA): 0.00", answer.text)
        self.assertNotIn("DIEGO", answer.text)

    def test_confirmed_code_never_falls_back_to_a_partial_match(self):
        answer = self.queries.execute("saldo", customer="SEGUR", codigo_exacto=True)
        self.assertEqual(answer.status, "needs_clarification")
        self.assertNotIn("TORTILLERIA", answer.text)

    def test_exact_match_is_listed_even_when_partial_matches_fill_the_limit(self):
        for i in range(12):
            self.db.customer(f"A{i:02d}", "SEGURA HERMANOS", "1")
        balances = self.queries.execute("saldos_coincidencias", customer="SEGURA")
        self.assertIn("(código SEGURA): 0.00", balances.text)
        choices = self.queries.execute("saldo", customer="SEGURA")
        self.assertIn("(código SEGURA)", choices.text)


class CountMatchesTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = CustomerQueries(self.db, {CUSTOMERS, PAYMENTS}, today=TODAY)

    def count(self, customer="SEGURA", **kwargs):
        return self.queries.execute("contar_coincidencias", customer=customer, **kwargs)

    def test_count_names_each_match_without_balances(self):
        self.db.customer("SEGURA", "TORTILLERIA SEGURA", "0")
        self.db.customer("DIEGO", "DIEGO SEGURA", "2194991.60")
        self.db.customer("OTRO", "Otra Persona", "9999")
        answer = self.count()
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.operation, "contar_coincidencias")
        self.assertIn("Hay 2 clientes", answer.text)
        self.assertIn("DIEGO SEGURA (código DIEGO)", answer.text)
        self.assertIn("TORTILLERIA SEGURA (código SEGURA)", answer.text)
        self.assertNotIn("Otra Persona", answer.text)
        self.assertNotIn("2,194,991.60", answer.text)
        self.assertNotIn("Saldo", answer.text)

    def test_one_match_reads_in_singular(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.assertIn("Hay 1 cliente ", self.count("Robles").text)

    def test_count_is_the_real_total_beyond_the_names_listed(self):
        for i in range(12):
            self.db.customer(f"R{i:02d}", "Ana Robles", "1")
        answer = self.count("Robles")
        self.assertIn("Hay 12 clientes", answer.text)
        self.assertEqual(answer.text.count("(código R"), 10)
        self.assertIn("Muestro 10", answer.text)

    def test_no_matches_asks_to_refine(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.assertEqual(self.count("Plaza").text, MATCHES_NOT_FOUND)

    def test_duplicate_codes_are_rejected(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.db.customer("R1", "Ana Robles", "7")
        with self.assertRaisesRegex(BusinessError, "repite"):
            self.count("Robles")

    def test_wildcards_are_literals(self):
        self.db.customer("R1", "Ana Robles", "5")
        self.assertEqual(self.count("%").text, MATCHES_NOT_FOUND)

    def test_dates_are_not_accepted(self):
        with self.assertRaises(BusinessError):
            self.count(period="este_mes")


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


class ConfirmedCodeTests(unittest.TestCase):
    completion = RankingGuardTests.completion
    OFFERED = [
        {"role": "user", "content": "Cuál es el saldo de Segura?"},
        {
            "role": "assistant",
            "content": "Encontré varios clientes:\n"
            "• DIEGO SEGURA (código DIEGO)\n"
            "• TORTILLERIA SEGURA (código SEGURA)\n"
            "¿Cuál quieres consultar?",
        },
    ]

    def ask(self, question, history=None):
        db = SyntheticDatabase()
        self.addCleanup(db.conn.close)
        db.customer("SEGURA", "TORTILLERIA SEGURA", "0")
        db.customer("DIEGO", "DIEGO SEGURA", "2194991.60")
        assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=db, client=Mock()
        )
        assistant.client.chat.completions.create.return_value = self.completion(
            "consultar_negocio",
            {"operation": "saldo", "customer": "SEGURA", "codigo_exacto": True},
        )
        return assistant.answer(question, history)

    def test_code_offered_by_the_assistant_resolves_the_pending_choice(self):
        answer = self.ask("SEGURA", self.OFFERED)
        self.assertIn("TORTILLERIA SEGURA (código SEGURA): 0.00", answer)

    def test_code_the_user_states_as_a_code_is_honoured(self):
        answer = self.ask("Saldo del código SEGURA")
        self.assertIn("TORTILLERIA SEGURA (código SEGURA): 0.00", answer)

    def test_model_cannot_claim_a_code_for_a_loose_surname(self):
        answer = self.ask("Cuál es el saldo de Segura?")
        self.assertIn("Encontré varios clientes", answer)
        self.assertIn("DIEGO SEGURA (código DIEGO)", answer)
        self.assertNotIn("0.00", answer)

    def test_tool_schema_exposes_the_new_operation_and_flag(self):
        schema = tool_definitions()[0]["function"]["parameters"]["properties"]
        self.assertEqual(schema["codigo_exacto"]["type"], "boolean")
        self.assertIn("contar_coincidencias", schema["operation"]["enum"])


if __name__ == "__main__":
    unittest.main()
