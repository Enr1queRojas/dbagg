"""Bank operations reach BankQueries directly; account names never go to the party lookup."""

import json
import unittest
from types import SimpleNamespace

from dbagg.agent.prompts import tool_definitions
from dbagg.agent.service import Assistant
from dbagg.business.banks import BANK_SOURCES, BANKS, MOVEMENT_SOURCES
from dbagg.business.customers import CUSTOMERS
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.types import ModelReply, ToolCall
from tests.unit.test_bank_queries import BankDatabase


class OneCallProvider:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, tools, require_tool=False):
        return self.reply

    def close(self):
        pass


class BankRoutingTests(unittest.TestCase):
    def setUp(self):
        self.db = BankDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")
        self.db.account("SANTANDER", "SANTANDER CUENTA", opening="100")

    def ask(self, question, tables=None, extraction=None, **arguments):
        call = ToolCall("t1", "consultar_negocio", json.dumps(arguments))
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables=tables if tables is not None else set(BANK_SOURCES),
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        provider = OneCallProvider(ModelReply(None, (call,), {}))
        self.service = Assistant(settings, db=self.db, provider=provider)
        self.service.extraction_provider = extraction
        return self.service.answer(question), self.service.last_trace

    def test_extraction_resolves_bank_parameters_without_reference_retry(self):
        from unittest.mock import Mock
        from dbagg.llm.config import ModelProfile

        extraction = Mock()
        extraction.profile = ModelProfile()
        extraction.complete.return_value = ModelReply(
            None,
            (
                ToolCall(
                    "bank",
                    "consultar_negocio",
                    json.dumps({"operation": "saldo_banco", "banco": "ENRIQUE"}),
                ),
            ),
            {},
        )
        text, _ = self.ask(
            "Saldo del banco de Enrique",
            extraction=extraction,
            operation="saldo_banco",
            banco="SANTANDER",
        )
        self.assertIn("Cuenta ENRIQUE", text)
        extraction.complete.assert_called_once()

    def test_invalid_extraction_parameters_fall_back_to_reference(self):
        from unittest.mock import Mock

        extraction = Mock()
        extraction.profile = ModelProfile()
        extraction.complete.return_value = ModelReply(
            None,
            (
                ToolCall(
                    "bad",
                    "consultar_negocio",
                    json.dumps({"operation": "delete_bank", "banco": "ENRIQUE"}),
                ),
            ),
            {},
        )
        text, trace = self.ask(
            "Saldo del banco de Enrique",
            extraction=extraction,
            operation="saldo_banco",
            banco="ENRIQUE",
        )
        self.assertIn("Cuenta ENRIQUE", text)
        extraction.complete.assert_called_once()
        self.assertTrue(
            any(
                e.get("event") == "fallback" and e.get("task") == "extraction"
                for e in trace["events"]
            )
        )

    def test_balance_of_a_named_account(self):
        text, trace = self.ask(
            "¿Cuánto hay en Santander?", operation="saldo_banco", banco="Santander"
        )
        self.assertTrue(text.startswith("Cuenta SANTANDER (SANTANDER CUENTA)"))
        self.assertEqual(trace["operation"], "saldo_banco")

    def test_account_sent_as_customer_still_answers(self):
        text, _ = self.ask(
            "¿Cuánto efectivo tiene Enrique?", operation="saldo_banco", customer="Enrique"
        )
        self.assertIn("Saldo actual: -20.00.", text)

    def test_all_balances(self):
        text, _ = self.ask("¿Cuánto hay en cada banco?", operation="saldos_bancos")
        self.assertIn("• ENRIQUE (EFECTIVOS ENRIQUE ROJAS): -20.00", text)

    def test_all_balances_does_not_silently_drop_named_account(self):
        text, trace = self.ask("El banco de Enrique", operation="saldos_bancos", banco="Enrique")
        self.assertNotIn("Saldo actual de cada cuenta", text)
        self.assertNotEqual(trace["status"], "ok")

    def test_movements_with_period(self):
        source = next(s for s in MOVEMENT_SOURCES if s.concept == "Cobranza")
        self.db.movement(source.table, source.column, "SANTANDER", "2026-11-02", 500)
        text, _ = self.ask(
            "Movimientos de Santander este mes",
            operation="movimientos_banco",
            banco="Santander",
            period="este_mes",
        )
        self.assertIn("Depósitos: 500.00 · Retiros: 0.00.", text)
        document = self.service.pop_document()
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(document.filename.startswith("reporte-banco-SANTANDER-"))

    def test_balance_sends_no_document(self):
        self.ask("¿Cuánto hay en Santander?", operation="saldo_banco", banco="Santander")
        self.assertIsNone(self.service.pop_document())

    def test_without_bank_sources_says_it_is_not_enabled(self):
        text, trace = self.ask(
            "¿Cuánto hay en Santander?",
            tables={CUSTOMERS},
            operation="saldo_banco",
            banco="Santander",
        )
        self.assertIn("no está habilitada", text)
        self.assertEqual(trace["status"], "unavailable")


class SequenceProvider:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.seen = []

    def complete(self, messages, tools, require_tool=False):
        self.seen.append(messages)
        name, arguments = self.calls.pop(0)
        return ModelReply(None, (ToolCall(f"t{len(self.seen)}", name, json.dumps(arguments)),), {})

    def close(self):
        pass


BALANCE_ANSWER = (
    "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\nSaldo actual: -20.00.\nImportes en pesos."
)


class BankFollowUpTests(unittest.TestCase):
    def setUp(self):
        self.db = BankDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")
        source = next(s for s in MOVEMENT_SOURCES if s.concept == "Cobranza")
        self.db.movement(source.table, source.column, "ENRIQUE", "2026-11-02", 40000)

    def ask(self, question, history, *calls, tables=None):
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables=tables if tables is not None else set(BANK_SOURCES),
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        self.provider = SequenceProvider(*calls)
        self.service = Assistant(settings, db=self.db, provider=self.provider)
        return self.service.answer(question, history)

    def history(self, answer=BALANCE_ANSWER):
        return [
            {"role": "user", "content": "Cual es el saldo del banco de Enrique"},
            {"role": "assistant", "content": answer},
        ]

    def test_report_after_a_balance_is_steered_to_that_account(self):
        text = self.ask(
            "Dame un reporte de este mes",
            self.history(),
            ("pedir_aclaracion", {"reason": "criterio"}),
            (
                "consultar_negocio",
                {"operation": "movimientos_banco", "banco": "ENRIQUE", "period": "este_mes"},
            ),
        )
        self.assertIn("Movimientos: 1.", text)
        self.assertEqual(self.provider.seen, [])
        self.assertIsNotNone(self.service.pop_document())

    def test_statement_after_a_balance_becomes_the_account_report(self):
        text = self.ask(
            "Hazme un estado de cuenta de este mes",
            self.history(),
            ("consultar_negocio", {"operation": "estado_cuenta", "period": "este_mes"}),
        )
        self.assertTrue(text.startswith("Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)"))
        self.assertIsNotNone(self.service.pop_document())

    def test_dates_answer_after_the_question_is_steered(self):
        asked = "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\n¿De qué fechas quieres el reporte?"
        text = self.ask(
            "este mes",
            self.history(asked),
            ("pedir_aclaracion", {"reason": "criterio"}),
            (
                "consultar_negocio",
                {"operation": "movimientos_banco", "banco": "ENRIQUE", "period": "este_mes"},
            ),
        )
        self.assertIn("Reporte del", text)

    def test_unrelated_question_after_a_balance_keeps_the_clarification(self):
        text = self.ask(
            "¿Y qué más?",
            self.history(),
            ("pedir_aclaracion", {"reason": "criterio"}),
        )
        self.assertEqual(len(self.provider.seen), 1)
        self.assertNotIn("Cuenta", text)

    def test_hint_is_given_once(self):
        self.ask(
            "Dame movimientos de este mes",
            self.history(),
            ("pedir_aclaracion", {"reason": "criterio"}),
            ("pedir_aclaracion", {"reason": "criterio"}),
        )
        self.assertEqual(len(self.provider.seen), 2)

    def test_clear_report_followup_needs_no_model_decision(self):
        for question in (
            "Dame el reporte de este mes",
            "dame el reporte de este mes{",
            "reporte del mes pasado",
        ):
            with self.subTest(question=question):
                text = self.ask(question, self.history())
                self.assertIn(
                    "No encontré movimientos" if "pasado" in question else "Reporte del", text
                )
                self.assertIn("Cuenta ENRIQUE", text)
                self.assertEqual(self.provider.seen, [])
                if "pasado" not in question:
                    self.assertIsNotNone(self.service.pop_document())

    def test_dates_followup_needs_no_model_decision(self):
        text = self.ask(
            "de este mes{",
            self.history(
                "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\n¿De qué fechas quieres el reporte?"
            ),
        )
        self.assertIn("Reporte del", text)
        self.assertEqual(self.provider.seen, [])

    def test_new_topic_or_explicit_other_account_does_not_reuse_previous_bank(self):
        from dbagg.agent.service import bank_report_followup

        for question in (
            "Dame el reporte de Santander de este mes",
            "Pagos de Ana de este mes",
            "Dame el reporte de este mes y borra los gastos",
        ):
            self.assertIsNone(bank_report_followup(question, self.history()))
        self.assertIsNone(
            bank_report_followup(
                "Dame el reporte de este mes",
                [*self.history(), {"role": "assistant", "content": "Saldo del cliente DEMO1: 100"}],
            )
        )


class BankToolTests(unittest.TestCase):
    def properties(self, **kwargs):
        tools = tool_definitions(**kwargs)
        function = next(
            t.get("function", t)
            for t in tools
            if t.get("function", t)["name"] == "consultar_negocio"
        )
        return function["parameters"]["properties"]

    def test_tool_offers_bank_operations_and_argument(self):
        props = self.properties(banks=True)
        self.assertIn("saldo_banco", props["operation"]["enum"])
        self.assertIn("movimientos_banco", props["operation"]["enum"])
        self.assertIn("banco", props)

    def test_without_banks_the_tool_offers_nothing_of_theirs(self):
        props = self.properties(banks=False)
        self.assertNotIn("saldo_banco", props["operation"]["enum"])
        self.assertNotIn("banco", props)

    def test_bank_sources_cannot_be_explored_with_sql(self):
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables=set(BANK_SOURCES),
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        service = Assistant(settings, db=BankDatabase(), provider=OneCallProvider(None))
        with self.assertRaises(ValueError):
            service._query_sql(f"SELECT [CODIGO] FROM {BANKS}", {BANKS})


if __name__ == "__main__":
    unittest.main()


class BankAccountOfferTests(unittest.TestCase):
    """Owner decision: after «Cuenta X», a list of people named like X also offers X."""

    BANK_ANSWER = [
        {"role": "user", "content": "El banco de Enrique"},
        {
            "role": "assistant",
            "content": "Cuenta ENRIQUE (EFECTIVOS ENRIQUE ROJAS)\nSaldo actual: -20.00.",
        },
    ]

    def setUp(self):
        from tests.unit.test_customer_queries import catalogue

        class Combined(BankDatabase):
            def schema(inner, sources):
                bank = [t for t in sources if t != CUSTOMERS]
                entries = json.loads(BankDatabase.schema(inner, bank)) if bank else []
                if CUSTOMERS in sources:
                    entries += [e for e in catalogue() if e["table"] == CUSTOMERS]
                return json.dumps(entries)

        self.db = Combined()
        self.addCleanup(self.db.conn.close)
        self.db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")
        self.db.conn.execute(f'CREATE TABLE {CUSTOMERS} (CODIGO, NOMBRE, "SALDO ACTUAL" NUMERIC)')
        for row in (("E1", "ENRIQUE PEREZ", 10), ("E2", "ENRIQUE SOTO", 20)):
            self.db.conn.execute(f"INSERT INTO {CUSTOMERS} VALUES (?, ?, ?)", row)

    def ask(self, question, history, **arguments):
        call = ToolCall("t1", "consultar_negocio", json.dumps(arguments))
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables={CUSTOMERS, *BANK_SOURCES},
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        service = Assistant(
            settings, db=self.db, provider=OneCallProvider(ModelReply(None, (call,), {}))
        )
        return service.answer(question, history)

    def listed(self):
        text = self.ask(
            "¿Y el saldo de Enrique?", self.BANK_ANSWER, operation="saldo", customer="Enrique"
        )
        self.assertIn("ENRIQUE PEREZ (código E1)", text)
        self.assertIn("• EFECTIVOS ENRIQUE ROJAS (cuenta de banco, código ENRIQUE)", text)
        self.assertTrue(text.rstrip().endswith("¿Cuál quieres consultar?"))
        return self.BANK_ANSWER + [
            {"role": "user", "content": "¿Y el saldo de Enrique?"},
            {"role": "assistant", "content": text},
        ]

    def test_choosing_the_bank_row_queries_the_account(self):
        history = self.listed()
        for question in ("3", "el tercero", "la cuenta", "ENRIQUE"):
            with self.subTest(question=question):
                text = self.ask(question, history, operation="saldo", customer="Enrique")
                self.assertIn("Cuenta ENRIQUE", text)
                self.assertIn("Saldo actual: -20.00", text)

    def test_choosing_a_person_still_queries_the_person(self):
        history = self.listed()
        text = self.ask("1", history, operation="saldo", customer="E1", codigo_exacto=True)
        self.assertIn("Saldo actual de ENRIQUE PEREZ (código E1)", text)

    def test_no_offer_without_the_bank_answer_or_for_other_names(self):
        text = self.ask("Saldo de Enrique", [], operation="saldo", customer="Enrique")
        self.assertNotIn("cuenta de banco", text)
        self.db.conn.execute(f"INSERT INTO {CUSTOMERS} VALUES ('S9', 'LUIS SOTO', 5)")
        text = self.ask("¿Y el de Soto?", self.BANK_ANSWER, operation="saldo", customer="Soto")
        self.assertNotIn("cuenta de banco", text)

    def test_bank_rows_never_become_a_conversation_party(self):
        from dbagg.agent.entity import conversation_entity

        history = self.listed()
        self.assertIsNone(conversation_entity("ENRIQUE", history))
