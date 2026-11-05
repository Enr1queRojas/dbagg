"""Who, then dates, then the document: the service asks; the model only chooses the operation."""

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from dbagg.agent.entity import conversation_entity
from dbagg.agent.prompts import tool_definitions
from dbagg.agent.service import Assistant
from dbagg.business.parties import CUSTOMER, SUPPLIER
from dbagg.business.statements import DocumentAttachment
from dbagg.business.suppliers import PURCHASES
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.types import ModelReply, ToolCall
from dbagg.services.conversation import DOCUMENT_FAILED, ConversationService
from tests.unit.test_statements import DEFINITIONS, StatementDatabase

RANGE = {"period": "rango", "start": "2026-08-30", "end": "2026-11-04"}


def assistant_message(content):
    return {"role": "assistant", "content": content}


class OneCallProvider:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, tools, require_tool=False):
        return self.reply

    def close(self):
        pass


class StatementFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = StatementDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.customer("R1", "5", name="Robles")
        self.db.customer("A1", "11", name="Ana")
        self.db.customer("X", "12", name="Otra Persona")
        self.db.supplier("P1", "22", name="Ana")
        self.db.supplier("X", "33", name="Ana Dos")
        self.db.supplier("C1", "100", name="Cuervo")
        self.db.line(PURCHASES, "C1", 1, "2026-10-01", "100", folio=7)
        self.service = None

    def ask(self, question=None, history=None, **arguments):
        call = ToolCall(
            "t1", "consultar_negocio", json.dumps({"operation": "estado_cuenta", **arguments})
        )
        settings = SimpleNamespace(
            model="test-model",
            api_key="synthetic",
            tables=set(DEFINITIONS),
            llm=LLMConfig(ModelProfile(model="test-model")),
        )
        provider = OneCallProvider(ModelReply(None, (call,), {}))
        self.service = Assistant(settings, db=self.db, provider=provider)
        if question is None:
            question = f"Estado de cuenta de {arguments.get('customer')}"
        return self.service.answer(question, history)

    def test_tool_offers_the_statement_operation(self):
        function = next(
            t.get("function", t)
            for t in tool_definitions()
            if t.get("function", t)["name"] == "consultar_negocio"
        )
        self.assertIn("estado_cuenta", function["parameters"]["properties"]["operation"]["enum"])

    def test_without_dates_asks_and_generates_nothing(self):
        text = self.ask(customer="Cuervo")
        self.assertTrue(text.startswith("Proveedor Cuervo (código C1)"))
        self.assertIn("¿De qué fechas", text)
        self.assertIsNone(self.service.pop_document())

    def test_period_todo_also_asks_for_dates(self):
        text = self.ask(customer="Cuervo", period="todo")
        self.assertIn("¿De qué fechas", text)
        self.assertIsNone(self.service.pop_document())

    def test_homonyms_are_cleared_before_dates(self):
        text = self.ask(customer="Ana", question="Estado de cuenta del proveedor Ana", **RANGE)
        self.assertIn("Encontré varios proveedores:", text)
        self.assertIsNone(self.service.pop_document())

    def test_name_in_both_catalogs_is_cleared_first(self):
        text = self.ask(customer="Ana", **RANGE)
        self.assertIn("como cliente y como proveedor", text)
        self.assertIsNone(self.service.pop_document())

    def test_customer_is_asked_with_a_context_line(self):
        text = self.ask(customer="Robles")
        self.assertEqual(text, "Estado de cuenta de Robles (código R1). ¿De qué fechas lo quieres?")

    def test_dates_in_the_request_generate_the_document(self):
        text = self.ask(customer="Cuervo", **RANGE)
        self.assertTrue(text.startswith("Proveedor Cuervo (código C1)"))
        self.assertIn("Estado de cuenta del 30/08/2026 al 04/11/2026.", text)
        self.assertIn("Saldo anterior: 0.00.", text)
        self.assertIn("Saldo al 04/11/2026: 100.00.", text)
        self.assertIn("Movimientos: 1.", text)
        document = self.service.pop_document()
        self.assertIsInstance(document, DocumentAttachment)
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertEqual(document.filename, "estado-cuenta-C1-2026-08-30-2026-11-04.pdf")
        self.assertIsNone(self.service.pop_document())

    def test_customer_statement_generates_the_document(self):
        text = self.ask(customer="Robles", **RANGE)
        self.assertTrue(text.startswith("Estado de cuenta de Robles (código R1)"))
        self.assertIsNotNone(self.service.pop_document())

    def test_warnings_reach_the_summary(self):
        self.db.supplier("DESC", "999", name="Descuadre")
        text = self.ask(customer="Descuadre", **RANGE)
        self.assertIn("no cuadran", text)
        self.assertIsNotNone(self.service.pop_document())

    def test_dates_follow_the_cleared_supplier(self):
        history = [
            assistant_message(
                "Proveedor Ana Dos (código X)\n¿De qué fechas quieres el estado de cuenta?"
            )
        ]
        text = self.ask(
            customer="X",
            codigo_exacto=True,
            question="este mes",
            history=history,
            period="este_mes",
        )
        self.assertTrue(text.startswith("Proveedor Ana Dos (código X)"))

    def test_dates_follow_the_cleared_customer(self):
        history = [
            assistant_message(
                "Estado de cuenta de Otra Persona (código X). ¿De qué fechas lo quieres?"
            )
        ]
        text = self.ask(
            customer="X",
            codigo_exacto=True,
            question="este mes",
            history=history,
            period="este_mes",
        )
        self.assertTrue(text.startswith("Estado de cuenta de Otra Persona (código X)"))

    def test_customer_context_survives_the_dates_question(self):
        history = [
            assistant_message("Estado de cuenta de Robles (código R1). ¿De qué fechas lo quieres?")
        ]
        self.assertEqual(conversation_entity("R1", history), CUSTOMER)
        self.assertEqual(conversation_entity("Robles", history), CUSTOMER)

    def test_supplier_summary_keeps_the_context(self):
        text = self.ask(customer="Cuervo", **RANGE)
        self.assertEqual(conversation_entity("C1", [assistant_message(text)]), SUPPLIER)

    def test_invalid_dates_generate_nothing(self):
        text = self.ask(customer="Cuervo", period="rango", start="ayer", end="hoy")
        self.assertIn("período", text)
        self.assertIsNone(self.service.pop_document())

    def test_new_answer_clears_an_unclaimed_document(self):
        self.ask(customer="Cuervo", **RANGE)
        service = self.service
        call = ToolCall("t2", "pedir_aclaracion", json.dumps({"reason": "escritura"}))
        service.provider = OneCallProvider(ModelReply(None, (call,), {}))
        service.answer("borra todo")
        self.assertIsNone(service.pop_document())

    def test_too_many_rows_asks_for_shorter_dates_without_a_document(self):
        self.db.supplier("MUCHOS", "2001", name="Muchos")
        for i in range(2001):
            self.db.line(PURCHASES, "MUCHOS", 100 + i, "2026-10-01", "1")
        text = self.ask(customer="Muchos", **RANGE)
        self.assertIn("fechas más cortas", text)
        self.assertIsNone(self.service.pop_document())


class ReviewedStatementFlowTests(StatementFlowTests):
    """Closed lists and threads must not change whose statement is delivered."""

    def closed_list(self, first_code, chosen_code):
        self.db.supplier(first_code, "1", name="Persona Uno")
        self.db.supplier(chosen_code, "2", name="Persona Dos")
        return [
            assistant_message(
                "Encontré varios proveedores:\n"
                f"• Persona Uno (código {first_code})\n"
                f"• Persona Dos (código {chosen_code})\n¿Cuál quieres consultar?"
            ),
            {"role": "user", "content": "Persona Dos"},
            assistant_message(
                f"Proveedor Persona Dos (código {chosen_code})\n"
                "¿De qué fechas quieres el estado de cuenta?"
            ),
        ]

    def test_date_words_do_not_reopen_a_closed_list(self):
        history = self.closed_list("JULIO", "J2")
        text = self.ask(
            customer="J2",
            codigo_exacto=True,
            question="del 1 de julio a hoy",
            history=history,
            **RANGE,
        )
        self.assertTrue(text.startswith("Proveedor Persona Dos (código J2)"))
        self.assertIn("J2", self.service.pop_document().filename)

    def test_a_numeric_code_does_not_match_a_day(self):
        history = self.closed_list("30", "J2")
        text = self.ask(
            customer="J2",
            codigo_exacto=True,
            question="del 30 de agosto a hoy",
            history=history,
            **RANGE,
        )
        self.assertTrue(text.startswith("Proveedor Persona Dos (código J2)"))

    def test_an_open_list_still_accepts_a_name(self):
        self.db.supplier("Q1", "1", name="Persona Uno")
        self.db.supplier("Q2", "2", name="Persona Dos")
        history = [
            assistant_message(
                "Encontré varios proveedores:\n• Persona Uno (código Q1)\n"
                "• Persona Dos (código Q2)\n¿Cuál quieres consultar?"
            )
        ]
        text = self.ask(customer="Q1", question="Persona Dos", history=history)
        self.assertTrue(text.startswith("Proveedor Persona Dos (código Q2)"))

    def test_a_document_belongs_to_the_thread_that_answered(self):
        self.ask(customer="Cuervo", **RANGE)
        service = self.service
        seen = []
        other = threading.Thread(target=lambda: seen.append(service.pop_document()))
        other.start()
        other.join()
        self.assertEqual(seen, [None])
        self.assertIsNotNone(service.pop_document())

    def test_another_thread_answering_does_not_clear_the_document(self):
        self.ask(customer="Cuervo", **RANGE)
        service = self.service
        call = ToolCall("t2", "pedir_aclaracion", json.dumps({"reason": "escritura"}))
        service.provider = OneCallProvider(ModelReply(None, (call,), {}))
        other = threading.Thread(target=lambda: service.answer("borra todo"))
        other.start()
        other.join()
        self.assertIsNotNone(service.pop_document())


class FakeAssistant:
    def __init__(self, text, document=None):
        self.text = text
        self.document = document
        self.last_trace = {}

    def answer(self, question, history=None):
        return self.text

    def pop_document(self):
        document, self.document = self.document, None
        return document


class DocumentDeliveryTests(unittest.TestCase):
    SUMMARY = "Proveedor Cuervo (código C1)\nEstado de cuenta del 01/10/2026 al 04/11/2026."
    DOCUMENT = DocumentAttachment(b"%PDF-privado", "estado.pdf")

    def setUp(self):
        self.calls = []
        self.sent_text = []

    def service(self, assistant, deliver_document):
        def deliver(sender, text, **_):
            self.calls.append("text")
            self.sent_text.append(text)

        settings = SimpleNamespace(feedback_enabled=False, gap_log_enabled=False, model="m")
        return ConversationService(settings, assistant, deliver, deliver_document=deliver_document)

    def test_document_goes_before_the_summary(self):
        def send_document(sender, content, filename):
            self.calls.append("document")
            self.assertEqual((sender, content, filename), ("521", b"%PDF-privado", "estado.pdf"))

        self.service(FakeAssistant(self.SUMMARY, self.DOCUMENT), send_document).process("521", "x")
        self.assertEqual(self.calls, ["document", "text"])
        self.assertEqual(self.sent_text, [self.SUMMARY])

    def test_failed_document_replaces_the_summary(self):
        service = self.service(
            FakeAssistant(self.SUMMARY, self.DOCUMENT), Mock(side_effect=RuntimeError("caída"))
        )
        service.process("521", "x")
        self.assertEqual(self.sent_text, [DOCUMENT_FAILED])
        memory = str(service.memory.history("521"))
        self.assertNotIn("Estado de cuenta del", memory)

    def test_missing_document_sender_is_a_failure(self):
        service = self.service(FakeAssistant(self.SUMMARY, self.DOCUMENT), None)
        service.process("521", "x")
        self.assertEqual(self.sent_text, [DOCUMENT_FAILED])

    def test_answers_without_a_document_are_unchanged(self):
        send_document = Mock()
        self.service(FakeAssistant("Hola"), send_document).process("521", "x")
        send_document.assert_not_called()
        self.assertEqual(self.sent_text, ["Hola"])

    def test_memory_and_text_hold_no_pdf_bytes(self):
        service = self.service(FakeAssistant(self.SUMMARY, self.DOCUMENT), Mock())
        service.process("521", "x")
        self.assertNotIn("%PDF", str(service.memory.history("521")))
        self.assertNotIn("%PDF", "".join(self.sent_text))

    def test_failure_log_has_no_private_data(self):
        service = self.service(
            FakeAssistant(self.SUMMARY, self.DOCUMENT), Mock(side_effect=RuntimeError("privado"))
        )
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            service.process("521", "x")
        output = "\n".join(logs.output)
        self.assertIn("document_failed", output)
        for secret in ("privado", "521", "estado.pdf"):
            self.assertNotIn(secret, output)
