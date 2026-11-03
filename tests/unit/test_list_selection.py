"""A list of customers selects none of them; the model's guess must not reach the database."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from dbagg.agent.intent import PERIOD_HINT, names_month_and_year
from dbagg.agent.service import (
    CLARIFICATIONS,
    Assistant,
    chosen_customer,
    customer_left_unselected,
)
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
            "¿cuál fue el segundo pago?",
            "el quinto",
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

    def test_asking_who_they_meant_keeps_the_list_open(self):
        history = RANKING + [
            {"role": "user", "content": "¿Cuál fue su último pago?"},
            {"role": "assistant", "content": CLARIFICATIONS["cliente"]},
        ]
        self.assertTrue(customer_left_unselected("R1", "¿Y su saldo?", history))
        self.assertEqual(chosen_customer("LUIS", "el segundo", history), "S2")

    def test_the_row_comes_from_the_list_not_from_the_model(self):
        self.assertEqual(chosen_customer("R1", "el segundo", RANKING), "S2")
        self.assertIsNone(chosen_customer("R1", "el quinto", RANKING))
        self.assertIsNone(chosen_customer("R1", "el segundo", SINGLE))
        self.assertIsNone(chosen_customer("R1", "saldo de R1, el segundo", RANKING))

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
        self.assistant.client.chat.completions.create.return_value = self.reply(arguments)
        return self.assistant.answer(question, history)

    @staticmethod
    def reply(arguments=None, name="consultar_negocio"):
        calls = []
        if arguments is not None:
            function = SimpleNamespace(name=name, arguments=json.dumps(arguments))
            calls = [SimpleNamespace(id="c1", function=function)]
        message = SimpleNamespace(content=None if calls else "texto", tool_calls=calls)
        reason = "tool_calls" if calls else "stop"
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, message=message)])

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

    def test_choosing_a_row_ignores_homonyms_and_the_models_guess(self):
        history = [
            {"role": "user", "content": "Los dos que más deben"},
            {
                "role": "assistant",
                "content": "Clientes con mayor saldo pendiente (hasta 2):\n"
                "1. HENRICH (R1): 200.00\n2. DIEGO SEGURA (DIEGO): 100.00",
            },
        ]
        self.ask("x", {"operation": "saldo", "customer": "R1"})
        self.db.customer("DIEGO", "DIEGO SEGURA", "100")
        self.db.customer("V-D", "VILLARREAL-DIEGO SEGURA", "5")
        reply = self.reply({"operation": "saldo", "customer": "DIEGO"})
        self.assistant.client.chat.completions.create.return_value = reply
        answer = self.assistant.answer("el segundo", history)
        self.assertIn("DIEGO SEGURA (código DIEGO)", answer)
        self.assertNotIn("Encontré varios", answer)

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


class ModelDecisionTests(unittest.TestCase):
    reply = staticmethod(ListSelectionGuardTests.reply)

    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer("R1", "ANA RUIZ", "200")
        self.assistant = Assistant(
            SimpleNamespace(model="test", tables={CUSTOMERS, PAYMENTS}), db=self.db, client=Mock()
        )
        self.create = self.assistant.client.chat.completions.create

    def test_a_reply_without_tools_is_asked_once_for_a_decision(self):
        self.create.side_effect = [
            self.reply(),
            self.reply({"reason": "criterio"}, "pedir_aclaracion"),
        ]
        answer = self.assistant.answer("saldo y último pago de Ruiz")
        self.assertIn("Necesito precisar", answer)
        self.assertEqual(self.create.call_args_list[0].kwargs["tool_choice"], "auto")
        self.assertEqual(self.create.call_args_list[1].kwargs["tool_choice"], "required")

    def test_a_provider_that_cannot_force_tools_keeps_the_safe_answer(self):
        self.create.side_effect = [self.reply(), RuntimeError("tool_choice not supported")]
        self.assertIn("No obtuve resultados", self.assistant.answer("hola"))

    def test_a_named_month_and_year_is_not_an_ambiguous_period(self):
        self.assertTrue(names_month_and_year("Pagos del 1 al 15 de octubre de 2026"))
        self.assertFalse(names_month_and_year("Pagos del 3 al 7"))
        self.assertFalse(names_month_and_year("Pagos del 3 al 7 de octubre"))
        arguments = {
            "operation": "pagos",
            "customer": "Ruiz",
            "period": "rango",
            "start": "2026-10-01",
            "end": "2026-10-15",
        }
        self.create.side_effect = [
            self.reply({"reason": "periodo"}, "pedir_aclaracion"),
            self.reply(arguments),
        ]
        answer = self.assistant.answer("Pagos de Ruiz del 1 al 15 de octubre de 2026")
        self.assertNotIn("¿Qué período", answer)
        hint = self.create.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(json.loads(hint["content"])["error"], PERIOD_HINT)

    def test_the_period_hint_is_given_only_once(self):
        self.create.return_value = self.reply({"reason": "periodo"}, "pedir_aclaracion")
        answer = self.assistant.answer("Pagos de Ruiz de octubre de 2026")
        self.assertIn("¿Qué período", answer)
        self.assertEqual(self.create.call_count, 2)

    def test_an_incomplete_date_is_still_asked(self):
        self.create.return_value = self.reply({"reason": "periodo"}, "pedir_aclaracion")
        self.assertIn("¿Qué período", self.assistant.answer("Pagos de Ruiz del 3 al 7"))
        self.assertEqual(self.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
