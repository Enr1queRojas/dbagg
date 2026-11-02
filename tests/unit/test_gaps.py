import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from dbagg.agent.service import Assistant
from dbagg.evaluation.cli import main
from dbagg.evaluation.gaps import GapStore, normalized
from dbagg.evaluation.store import FeedbackStore, utc_now
from dbagg.services.conversation import ConversationService

SENDER = "5215555555555"
CASE = {
    "anonymized": True,
    "id": "notas-por-vencer",
    "question": "¿Qué notas de Ana Demo vencen esta semana?",
    "history": [],
    "expected_tool": "pedir_aclaracion",
    "expected_arguments": {"reason": "fuera_alcance"},
    "criteria": "Sin contrato de vencimientos; no inventar plazos.",
}


APPROVED_CASE = {
    "anonymized": True,
    "verified": True,
    "messages": [
        {"role": "user", "content": "saldo de Cliente demo"},
        {"role": "assistant", "content": "Cliente demo debe 1."},
    ],
}


def trace(gap, **extra):
    return dict({"gap": gap, "model": "m", "prompt_version": "p:1", "sources": []}, **extra)


class GapStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = GapStore(self.folder / "gaps.sqlite3")

    def test_same_question_variants_are_grouped_by_frequency(self):
        self.store.record("¿Cuáles notas vencen?", trace("fuera_alcance"))
        self.store.record("cuales notas vencen", trace("fuera_alcance"), voice=True)
        self.store.record("Ventas por sucursal", trace("exploratoria", sources=["demo.v"]))
        groups = self.store.grouped()
        self.assertEqual(groups[0]["count"], 2)
        self.assertEqual(groups[0]["category"], "fuera_alcance")
        self.assertIn("demo.v", self.store.grouped("exploratoria")[0]["sources"])
        self.assertEqual(normalized("¿Cuáles  notas?"), "cuales notas")

    def test_summary_has_counts_but_no_question_text(self):
        self.store.record("Pregunta privada de Ana Demo", trace("sin_evidencia"))
        summary = json.dumps(self.store.summary(), ensure_ascii=False)
        self.assertIn('"count": 1', summary)
        self.assertNotIn("Ana", summary)

    def test_review_covers_the_whole_group_and_requires_an_anonymized_case(self):
        first = self.store.record("¿Cuáles notas vencen?", trace("fuera_alcance"))
        self.store.record("cuales notas vencen", trace("fuera_alcance"))
        with self.assertRaisesRegex(ValueError, "anonymized"):
            self.store.review(first, "nuevo_caso", "revisor", case=dict(CASE, anonymized=False))
        self.assertEqual(self.store.review(first, "nuevo_caso", "revisor", case=CASE), 2)
        self.assertEqual(self.store.grouped(), [])

    def test_discarded_question_never_returns_to_review(self):
        gap = self.store.record("¿Cuánto maíz hay en cada bodega?", trace("exploratoria"))
        self.store.review(gap, "descartado", "revisor", note="Sin dato de bodega")
        self.store.record("cuanto maiz hay en cada bodega", trace("fuera_alcance"))
        self.store.record("¿Cuáles notas vencen?", trace("fuera_alcance"))
        self.assertEqual([g["example"] for g in self.store.grouped()], ["¿Cuáles notas vencen?"])

    def test_export_writes_eval_shaped_synthetic_cases_without_overwriting(self):
        gap = self.store.record("¿Cuáles notas vencen?", trace("fuera_alcance"))
        self.store.review(gap, "nuevo_caso", "revisor", case=CASE)
        output = self.folder / "export.json"
        self.assertEqual(self.store.export(output), 1)
        dataset = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(dataset["data_kind"], "synthetic")
        self.assertEqual(dataset["cases"][0]["id"], "notas-por-vencer")
        self.assertNotIn("anonymized", dataset["cases"][0])
        with self.assertRaises(FileExistsError):
            self.store.export(output)

    def test_cli_lists_and_reviews(self):
        gap = self.store.record("¿Cuáles notas vencen?", trace("fuera_alcance"))
        db = ["--gaps-db", str(self.store.path)]
        main(db + ["gaps-review", gap, "--decision", "descartado", "--reviewer", "r"])
        self.assertEqual(self.store.grouped(), [])


class RetentionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def age(self, path, table, row_id, days=200):
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        connection = sqlite3.connect(path)
        with connection:
            connection.execute(f"UPDATE {table} SET created_at=? WHERE id=?", (old, row_id))
        connection.close()

    def test_old_gaps_are_deleted_and_reviewed_ones_keep_only_what_review_needs(self):
        store = GapStore(self.folder / "gaps.sqlite3")
        old = store.record("¿Cuánto debe Ana Demo?", trace("fuera_alcance"))
        discarded = store.record("¿Cuánto maíz hay en cada bodega?", trace("exploratoria"))
        store.review(discarded, "descartado", "revisor", note="Ana Demo")
        recent = store.record("¿Qué notas vencen?", trace("fuera_alcance"))
        for gap in (old, discarded):
            self.age(store.path, "gaps", gap)
        self.assertEqual(store.purge(90), 2)
        with store._connect() as connection:
            rows = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM gaps")}
        self.assertNotIn(old, rows)
        self.assertEqual(rows[discarded]["question"], "[depurado]")
        self.assertIsNone(rows[discarded]["note"])
        self.assertEqual(rows[recent]["question"], "¿Qué notas vencen?")
        # The discard still applies to repeats after the text was purged.
        store.record("cuanto maiz hay en cada bodega", trace("exploratoria"))
        self.assertEqual([g["example"] for g in store.grouped()], ["¿Qué notas vencen?"])
        with self.assertRaises(ValueError):
            store.purge(0)

    def test_old_feedback_is_deleted_unless_approved_and_then_only_the_case_remains(self):
        store = FeedbackStore(self.folder / "feedback.sqlite3")
        interaction = {
            "created_at": utc_now(),
            "question": "saldo de Ana Demo",
            "answer": "Debe 100",
            "history": [{"role": "user", "content": "Ana Demo"}],
            "model": "m",
            "prompt_version": "p:1",
        }
        store.record(dict(interaction, id="old"), "bad", "Ana Demo")
        store.record(dict(interaction, id="kept"), "good")
        store.review("kept", "approved", "revisor", case=APPROVED_CASE)
        for row_id in ("old", "kept"):
            self.age(store.path, "feedback", row_id)
        self.assertEqual(store.purge(90), 2)
        kept = store.get("kept")
        self.assertEqual((kept["question"], kept["answer"]), ("[depurado]", "[depurado]"))
        self.assertEqual((kept["history_json"], kept["reason"]), ("[]", ""))
        self.assertIn("Cliente demo", kept["prepared_case_json"])
        with self.assertRaises(ValueError):
            store.get("old")


class GapCaptureTests(unittest.TestCase):
    def completion(self, name=None, arguments=None, content=None):
        calls = (
            []
            if name is None
            else [
                SimpleNamespace(
                    id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
                )
            ]
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls" if calls else "stop",
                    message=SimpleNamespace(content=content, tool_calls=calls),
                )
            ]
        )

    def assistant(self, *replies):
        assistant = Assistant(
            SimpleNamespace(model="test", tables={"proadel.gastos_data"}), db=Mock(), client=Mock()
        )
        assistant.client.chat.completions.create.side_effect = list(replies)
        return assistant

    def test_agent_marks_out_of_scope_and_no_evidence(self):
        assistant = self.assistant(self.completion("pedir_aclaracion", {"reason": "fuera_alcance"}))
        assistant.answer("¿Qué notas vencen esta semana?")
        self.assertEqual(assistant.last_trace["gap"], "fuera_alcance")
        assistant = self.assistant(self.completion(content="Debe 999"))
        assistant.answer("¿Cuánto debe?")
        self.assertEqual(assistant.last_trace["gap"], "sin_evidencia")
        assistant = self.assistant(self.completion("pedir_aclaracion", {"reason": "cliente"}))
        assistant.answer("su saldo")
        self.assertNotIn("gap", assistant.last_trace)

    def test_conversation_logs_gaps_only_and_survives_log_failures(self):
        gaps, deliver, assistant = Mock(), Mock(), Mock()
        assistant.answer.return_value = "Ese reporte todavía no tiene una definición..."
        assistant.last_trace = trace("fuera_alcance")
        settings = SimpleNamespace(model="m", feedback_enabled=False, feedback_path=None)
        service = ConversationService(settings, assistant, deliver, gap_store=gaps)
        service.process(SENDER, "¿Qué notas vencen?")
        gaps.record.assert_called_once_with("¿Qué notas vencen?", assistant.last_trace, voice=False)
        assistant.last_trace = {"status": "answered"}
        service.process(SENDER, "saldo de Ana")
        gaps.record.assert_called_once()
        assistant.last_trace = trace("fuera_alcance")
        gaps.record.side_effect = OSError("disco lleno")
        service.process(SENDER, "otra pregunta")
        self.assertEqual(deliver.call_count, 3)


if __name__ == "__main__":
    unittest.main()
