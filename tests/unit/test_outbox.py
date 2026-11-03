"""Tests unitarios para OutboxStore y PersistentMessageGate."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from dbagg.integrations.meta import MetaClient
from dbagg.services.conversation import ConversationService
from dbagg.services.outbox import OutboxStore, PersistentMessageGate


def _make_store(tmp_dir):
    path = Path(tmp_dir) / "outbox.sqlite3"
    return OutboxStore(path)


class TestOutboxStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_enqueue_and_pending(self):
        self.store.enqueue("sender1", "hola")
        self.store.enqueue("sender2", "mundo")
        rows = self.store.pending()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["status"], "pendiente")

    def test_mark_sent_stores_wamid(self):
        row_id = self.store.enqueue("sender1", "hola")
        self.store.mark_sent(row_id, "wamid_abc123")
        self.assertEqual(len(self.store.pending()), 0)
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(row["status"], "enviado")
        self.assertEqual(row["wamid"], "wamid_abc123")

    def test_mark_failed(self):
        row_id = self.store.enqueue("sender1", "hola")
        self.store.mark_failed(row_id)
        self.assertEqual(len(self.store.pending()), 0)
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM outbox WHERE id=?", (row_id,)).fetchone()
        self.assertEqual(row["status"], "fallido")

    def test_purge_removes_only_old_sent(self):
        # Fila enviada con fecha hace 100 días
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with self.store._connect() as conn:
            conn.execute(
                "INSERT INTO outbox (sender, body, status, attempts, created_at, updated_at) "
                "VALUES (?, ?, 'enviado', 1, ?, ?)",
                ("s", "body", old_ts, old_ts),
            )

        # Fila pendiente reciente (no debe borrarse)
        self.store.enqueue("s2", "nueva")

        deleted = self.store.purge(90)
        self.assertEqual(deleted, 1)
        # La fila pendiente sigue ahí
        self.assertEqual(len(self.store.pending()), 1)

    def test_migrate_idempotent(self):
        path = Path(self._tmp.name) / "idempotent.sqlite3"
        # Construir dos veces con el mismo path no debe lanzar excepciones
        OutboxStore(path)
        OutboxStore(path)


class TestPersistentMessageGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = _make_store(self._tmp.name)
        self.gate = PersistentMessageGate(self.store)

    def tearDown(self):
        self._tmp.cleanup()

    def test_persistent_gate_deduplication(self):
        self.assertTrue(self.gate.claim("id1", "sender_a"))
        self.assertFalse(self.gate.claim("id1", "sender_a"))

    def test_persistent_gate_rate_limit(self):
        # Primer mensaje del sender: debe pasar
        self.assertTrue(self.gate.claim("id_ratelimit_1", "sender_b"))
        # Segundo mensaje distinto del mismo sender en < 10 s: debe rechazarse
        self.assertFalse(self.gate.claim("id_ratelimit_2", "sender_b"))

    def test_persistent_gate_control_always_passes(self):
        # Los mensajes de control ignoran el rate-limit
        self.assertTrue(self.gate.claim("ctrl1", "sender_c", control=True))
        # Un control adicional del mismo sender pasa sin limitación de rate
        self.assertTrue(self.gate.claim("ctrl2", "sender_c", control=True))

    def test_persistent_gate_rate_limit_expired(self):
        """Un segundo mensaje del mismo sender pasa después de 10 s."""
        self.assertTrue(self.gate.claim("id_old", "sender_d"))
        self.assertFalse(self.gate.claim("id_soon", "sender_d"))
        self._age_last_question("sender_d", 11)
        self.assertTrue(self.gate.claim("id_new", "sender_d"))

    def test_a_command_does_not_delay_the_next_question(self):
        self.assertTrue(self.gate.claim("ctrl", "sender_e", control=True))
        self.assertTrue(self.gate.claim("question", "sender_e"))

    def test_a_rejected_message_does_not_restart_the_wait(self):
        self.assertTrue(self.gate.claim("first", "sender_f"))
        self._age_last_question("sender_f", 7)
        self.assertFalse(self.gate.claim("rejected", "sender_f"))
        self._age_last_question("sender_f", 11)
        self.assertTrue(self.gate.claim("retry", "sender_f"))

    def test_a_rejected_message_is_still_deduplicated(self):
        self.assertTrue(self.gate.claim("first", "sender_g"))
        self.assertFalse(self.gate.claim("rejected", "sender_g"))
        self._age_last_question("sender_g", 11)
        self.assertFalse(self.gate.claim("rejected", "sender_g"))

    def test_the_wait_survives_a_restart(self):
        self.assertTrue(self.gate.claim("first", "sender_h"))
        restarted = PersistentMessageGate(OutboxStore(self.store.path))
        self.assertFalse(restarted.claim("second", "sender_h"))

    def _age_last_question(self, sender, seconds):
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE sender_rate SET last_at=? WHERE sender=?", (time.time() - seconds, sender)
            )


class TestDelivery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = _make_store(self._tmp.name)
        self.sent = []
        self.status = 200
        settings = SimpleNamespace(
            graph_version="v1",
            phone_id="1",
            meta_token="t",
            feedback_enabled=False,
            gap_log_enabled=False,
            model="test",
            data_retention_days=90,
        )
        meta = MetaClient(settings, transport=httpx.MockTransport(self._meta))
        self.settings, self.meta = settings, meta
        self.conversation = ConversationService(
            settings, SimpleNamespace(), meta.send, outbox=self.store
        )

    def _meta(self, request):
        self.sent.append(json.loads(request.content)["text"]["body"])
        return httpx.Response(self.status, json={"messages": [{"id": f"w{len(self.sent)}"}]})

    def _statuses(self):
        with self.store._connect() as conn:
            return [r["status"] for r in conn.execute("SELECT status FROM outbox ORDER BY id")]

    def _age_rows(self, hours):
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.store._connect() as conn:
            conn.execute("UPDATE outbox SET created_at=?", (old,))

    def test_recovery_sends_a_pending_answer_exactly_once(self):
        self.store.enqueue("s", "respuesta interrumpida")
        for _ in range(3):
            self.conversation.recover_pending()
        self.assertEqual(self.sent, ["respuesta interrumpida"])
        self.assertEqual(self._statuses(), ["enviado"])

    def test_an_old_pending_answer_is_never_delivered(self):
        self.store.enqueue("s", "saldo de hace dias")
        self._age_rows(2)
        self.conversation.recover_pending()
        self.assertEqual(self.sent, [])
        self.assertEqual(self._statuses(), ["fallido"])

    def test_a_rejected_send_is_closed_and_not_resent(self):
        self.status = 400
        self.conversation._send("s", "mensaje")
        self.assertEqual(self._statuses(), ["fallido"])
        self.conversation.recover_pending()
        self.assertEqual(len(self.sent), 1)

    def test_a_normal_send_is_recorded_once(self):
        self.assertTrue(self.conversation._send("s", "hola"))
        self.assertEqual(self.sent, ["hola"])
        self.assertEqual(self._statuses(), ["enviado"])

    def test_startup_retention_covers_the_outbox_and_deduplication(self):
        self.store.enqueue("s", "importe antiguo")
        self.store.mark_sent(1, "w1")
        self._age_rows(24 * 91)
        now = datetime.now(timezone.utc)
        with self.store._connect() as conn:
            for message_id, days in (("recent", 2), ("old", 9)):
                conn.execute(
                    "INSERT INTO seen_messages (message_id, sender, seen_at) VALUES (?, 's', ?)",
                    (message_id, (now - timedelta(days=days)).isoformat()),
                )
        ConversationService(self.settings, SimpleNamespace(), self.meta.send, outbox=self.store)
        self.assertEqual(self._statuses(), [])
        with self.store._connect() as conn:
            rows = conn.execute("SELECT message_id FROM seen_messages").fetchall()
        self.assertEqual([r["message_id"] for r in rows], ["recent"])


if __name__ == "__main__":
    unittest.main()
