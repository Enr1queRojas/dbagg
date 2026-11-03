"""A list of customers selects none of them; the model's guess must not reach the database."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from dbagg.agent.service import Assistant, customer_left_unselected
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS, SyntheticDatabase

RANKING = [
    {"role": "user", "content": "Los dos que más deben"},
    {
        "role": "assistant",
        "content": "Clientes con mayor saldo pendiente (hasta 2):\n"
        "1. ANA RUIZ (R1): 200.00\n"
        "2. LUIS SOTO (S2): 100.00\n"
        "Importes sin moneda confirmada. Los empates se ordenan por código.",
    },
]
SINGLE = [
    {"role": "user", "content": "Saldo de Ruiz"},
    {"role": "assistant", "content": "Saldo actual de ANA RUIZ (código R1): 200.00."},
]


class CustomerLeftUnselectedTests(unittest.TestCase):
    def test_follow_ups_that_choose_nobody_are_unselected(self):
        for question in (
            "¿Cuál fue su último pago?",
            "¿Y cuánto pagó este mes?",
            "sí",
            "¿y su primer pago?",
            "¿cuánto pagó el primero de octubre?",
        ):
            with self.subTest(question):
                self.assertTrue(customer_left_unselected("R1", question, RANKING))

    def test_naming_or_pointing_at_one_customer_selects_it(self):
        for question in (
            "¿Y R1?",
            "saldo de r1",
            "el primero",
            "¿cuánto pagó la segunda?",
            "del segundo",
            "el número 2",
        ):
            with self.subTest(question):
                self.assertFalse(customer_left_unselected("R1", question, RANKING))

    def test_every_format_that_lists_customers_counts(self):
        for content in (
            "Encontré varios clientes:\n• ANA RUIZ (código R1)\n• LUIS SOTO (código S2)",
            "Saldo actual de cada cliente:\n1. ANA RUIZ (código R1): 200.00\n"
            "2. LUIS SOTO (código S2): 100.00",
        ):
            with self.subTest(content):
                history = [{"role": "assistant", "content": content}]
                self.assertTrue(customer_left_unselected("R1", "¿y su saldo?", history))

    def test_movements_of_one_customer_are_not_a_list_of_customers(self):
        history = [
            {
                "role": "assistant",
                "content": "Pagos activos de ANA RUIZ (código R1):\n"
                "• 2026-10-01 · 50.00 · folio F1\n• 2026-10-02 · 60.00 · folio F2",
            }
        ]
        self.assertFalse(customer_left_unselected("R1", "¿y su saldo?", history))

    def test_only_the_latest_answer_counts(self):
        self.assertFalse(customer_left_unselected("R1", "¿Y su último pago?", SINGLE))
        self.assertFalse(customer_left_unselected("R1", "¿Y su último pago?", RANKING + SINGLE))
        self.assertFalse(customer_left_unselected("R1", "¿Y su último pago?", None))
        self.assertTrue(customer_left_unselected("R1", "¿Y su último pago?", SINGLE + RANKING))


class ListSelectionGuardTests(unittest.TestCase):
    def ask(self, question, arguments, history=RANKING):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer("R1", "ANA RUIZ", "200")
        self.db.customer("S2", "LUIS SOTO", "100")
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=self.db, client=Mock()
        )
        call = SimpleNamespace(
            id="c1",
            function=SimpleNamespace(name="consultar_negocio", arguments=json.dumps(arguments)),
        )
        self.assistant.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(content=None, tool_calls=[call]),
                )
            ]
        )
        return self.assistant.answer(question, history)

    def test_guessing_the_first_listed_customer_asks_instead(self):
        answer = self.ask(
            "¿Y cuánto pagó este mes?",
            {"operation": "total_pagos", "customer": "R1", "period": "este_mes"},
        )
        self.assertIn("¿De qué cliente", answer)
        self.assertNotIn("ANA RUIZ", answer)
        self.assertEqual(self.assistant.last_trace["status"], "needs_clarification")
        self.assertEqual(self.assistant.last_trace["clarification"], "cliente")

    def test_an_explicit_choice_is_answered(self):
        answer = self.ask(
            "el primero", {"operation": "saldo", "customer": "R1", "codigo_exacto": True}
        )
        self.assertIn("ANA RUIZ", answer)
        self.assertIn("200.00", answer)

    def test_the_whole_list_can_still_be_requested(self):
        history = [
            {"role": "user", "content": "Saldo de Ana Robles"},
            {
                "role": "assistant",
                "content": "Encontré varios clientes:\n"
                "• ANA RUIZ (código R1)\n• ANA SOTO (código S2)\n¿Cuál quieres consultar?",
            },
            {"role": "user", "content": "Revisa a ANA"},
            {
                "role": "assistant",
                "content": "Encontré varios clientes:\n"
                "• ANA RUIZ (código R1)\n• ANA SOTO (código S2)\n¿Cuál quieres consultar?",
            },
        ]
        answer = self.ask(
            "dame sus saldos", {"operation": "saldos_coincidencias", "customer": "ANA"}, history
        )
        self.assertNotIn("¿De qué cliente", answer)


if __name__ == "__main__":
    unittest.main()
