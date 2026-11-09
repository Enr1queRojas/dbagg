"""The routing eval scores the model's first decision; only the model is simulated here."""

import json
import unittest
from datetime import date
from types import SimpleNamespace

from dbagg.agent.service import Assistant
from dbagg.evaluation.routing import matches, observe, run
from dbagg.llm.config import LLMConfig, ModelProfile
from dbagg.llm.types import ModelReply, ToolCall
from dbagg.business.banks import BANK_SOURCES
from tests.unit.test_customer_queries import CUSTOMERS, PAYMENTS


def case(**fields):
    return {"id": "c1", "question": "¿Cuánto debe DEMO1?", **fields}


def reply(name, **arguments):
    call = ToolCall("t1", name, json.dumps(arguments))
    return ModelReply(None, (call,), {})


class FakeProvider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.tools = []

    def complete(self, messages, tools, require_tool=False):
        self.calls.append(messages)
        self.tools.append(tools)
        next_reply = self.replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply

    def close(self):
        pass


class NoDatabase:
    def __getattr__(self, name):
        raise AssertionError("La evaluación de enrutamiento no debe consultar la base.")


def assistant(provider, tables=None):
    settings = SimpleNamespace(
        model="test-model",
        api_key="synthetic",
        tables=tables or {CUSTOMERS, PAYMENTS},
        llm=LLMConfig(ModelProfile(model="test-model")),
    )
    return Assistant(settings, db=NoDatabase(), provider=provider)


SALDO = {
    "expected_tool": "consultar_negocio",
    "expected_arguments": {"operation": "saldo", "customer": "DEMO1"},
}


class MatchesTests(unittest.TestCase):
    def test_same_tool_and_arguments_match(self):
        self.assertTrue(matches(SALDO, "consultar_negocio", dict(SALDO["expected_arguments"])))

    def test_a_different_tool_does_not_match(self):
        self.assertFalse(matches(SALDO, "consultar_sql", dict(SALDO["expected_arguments"])))

    def test_no_tool_call_does_not_match(self):
        self.assertFalse(matches(SALDO, None, {}))

    def test_an_argument_the_case_does_not_expect_does_not_match(self):
        ranking = {
            "expected_tool": "consultar_negocio",
            "expected_arguments": {"operation": "ranking_deuda", "limit": 3},
        }
        arguments = {"operation": "ranking_deuda", "limit": 3, "customer": "DEMO1"}
        self.assertFalse(matches(ranking, "consultar_negocio", arguments))

    def test_a_missing_expected_argument_does_not_match(self):
        self.assertFalse(matches(SALDO, "consultar_negocio", {"operation": "saldo"}))

    def test_explicit_defaults_equal_omitted_arguments(self):
        arguments = {
            "operation": "saldo",
            "customer": "DEMO1",
            "period": "todo",
            "limit": 5,
            "codigo_exacto": False,
            "comparar": "periodo_anterior",
            "start": None,
            "end": "",
        }
        self.assertTrue(matches(SALDO, "consultar_negocio", arguments))

    def test_a_default_the_case_expects_is_still_required(self):
        expected = {"operation": "ranking_deuda", "limit": 5}
        ranking = {"expected_tool": "consultar_negocio", "expected_arguments": expected}
        self.assertTrue(matches(ranking, "consultar_negocio", {"operation": "ranking_deuda"}))
        self.assertFalse(
            matches(ranking, "consultar_negocio", {"operation": "ranking_deuda", "limit": 3})
        )

    def test_names_ignore_case_and_accents(self):
        stock = {
            "expected_tool": "consultar_negocio",
            "expected_arguments": {"operation": "existencias", "producto": "maíz"},
        }
        self.assertTrue(
            matches(stock, "consultar_negocio", {"operation": "existencias", "producto": "MAIZ"})
        )
        self.assertTrue(
            matches(SALDO, "consultar_negocio", {"operation": "saldo", "customer": "demo1"})
        )

    def test_exact_code_must_be_sent_when_expected(self):
        exact = {
            "expected_tool": "consultar_negocio",
            "expected_arguments": {"operation": "saldo", "customer": "DEMO", "codigo_exacto": True},
        }
        loose = {"operation": "saldo", "customer": "DEMO"}
        self.assertFalse(matches(exact, "consultar_negocio", loose))


class ObserveTests(unittest.TestCase):
    def test_sends_the_case_history_and_the_dataset_calendar_to_the_model(self):
        provider = FakeProvider(reply("consultar_negocio", operation="saldo", customer="DEMO1"))
        history = [
            {"role": "user", "content": "Consulta DEMO1"},
            {"role": "assistant", "content": "Saldo actual de DEMO1: 100.00."},
        ]
        tool, arguments = observe(assistant(provider), case(history=history), date(2026, 10, 8))
        self.assertEqual(tool, "consultar_negocio")
        self.assertEqual(arguments, {"operation": "saldo", "customer": "DEMO1"})
        messages = provider.calls[0]
        self.assertEqual(messages[1:3], history)
        self.assertEqual(messages[-1]["content"], "¿Cuánto debe DEMO1?")
        self.assertIn('"today": "2026-10-08"', messages[0]["content"])

    def test_offers_the_operations_the_service_would_offer(self):
        def operations(tables):
            provider = FakeProvider(reply("consultar_negocio", operation="saldos_bancos"))
            observe(assistant(provider, tables), case(), date(2026, 10, 8))
            tool = next(
                t["function"]
                for t in provider.tools[0]
                if t["function"]["name"] == "consultar_negocio"
            )
            return tool["parameters"]["properties"]["operation"]["enum"]

        self.assertIn("saldo_banco", operations({CUSTOMERS, PAYMENTS, *BANK_SOURCES}))
        self.assertNotIn("saldo_banco", operations({CUSTOMERS, PAYMENTS}))
        self.assertNotIn("movimientos", operations({CUSTOMERS, PAYMENTS}))

    def test_exact_code_flag_counts_only_when_the_service_would_honor_it(self):
        flagged = dict(operation="saldo", customer="DEMO1", codigo_exacto=True)
        provider = FakeProvider(reply("consultar_negocio", **flagged))
        _, arguments = observe(assistant(provider), case(), date(2026, 10, 8))
        self.assertFalse(arguments["codigo_exacto"])
        provider = FakeProvider(reply("consultar_negocio", **flagged))
        history = [
            {"role": "user", "content": "Saldo de Demo"},
            {"role": "assistant", "content": "Saldo de CLIENTE DEMO (código DEMO1): 100.00."},
        ]
        _, arguments = observe(
            assistant(provider), case(question="¿Y su saldo?", history=history), date(2026, 10, 8)
        )
        self.assertTrue(arguments["codigo_exacto"])

    def test_a_reply_without_tools_is_observed_as_no_tool(self):
        provider = FakeProvider(ModelReply("Debe 999.", (), {}), ModelReply("Debe 999.", (), {}))
        self.assertEqual(observe(assistant(provider), case(), date(2026, 10, 8)), (None, {}))

    def test_comparison_answered_by_the_service_never_reaches_the_model(self):
        provider = FakeProvider()
        question = "¿Cuál de los dos debe más?"
        observed = observe(assistant(provider), case(question=question), date(2026, 10, 8))
        self.assertEqual(observed, ("pedir_aclaracion", {"reason": "comparacion"}))
        self.assertEqual(provider.calls, [])


class RunTests(unittest.TestCase):
    def dataset(self, *cases):
        return {"business_today": "2026-10-08", "cases": list(cases)}

    def test_reports_each_run_and_keeps_going_after_a_provider_error(self):
        provider = FakeProvider(
            RuntimeError("clave-secreta en el mensaje"),
            reply("consultar_negocio", operation="saldo", customer="DEMO1"),
        )
        results = run(
            assistant(provider),
            self.dataset(case(id="falla", **SALDO), case(id="pasa", **SALDO)),
        )
        self.assertEqual([r["id"] for r in results], ["falla", "pasa"])
        self.assertEqual([r["passed"] for r in results], [0, 1])
        self.assertEqual(results[0]["errors"], ["RuntimeError"])
        self.assertNotIn("clave-secreta", json.dumps(results))

    def test_repeat_counts_runs_that_disagree(self):
        good = reply("consultar_negocio", operation="saldo", customer="DEMO1")
        provider = FakeProvider(good, reply("pedir_aclaracion", reason="cliente"), good)
        result = run(assistant(provider), self.dataset(case(**SALDO)), repeat=3)[0]
        self.assertEqual((result["passed"], result["runs"]), (2, 3))
        self.assertEqual(result["observed"], [["pedir_aclaracion", {"reason": "cliente"}]])

    def test_only_runs_the_requested_cases(self):
        provider = FakeProvider(reply("consultar_negocio", operation="saldo", customer="DEMO1"))
        results = run(
            assistant(provider),
            self.dataset(case(id="uno", **SALDO), case(id="dos", **SALDO)),
            only={"dos"},
        )
        self.assertEqual([r["id"] for r in results], ["dos"])


if __name__ == "__main__":
    unittest.main()
