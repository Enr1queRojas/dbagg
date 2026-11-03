"""Tests unitarios para OutboxStore y PersistentMessageGate."""

import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        # Insertar directamente un seen_message con seen_at hace 11 s para simular el gap
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=11)).isoformat()
        with self.store._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_messages (message_id, sender, seen_at) VALUES (?, ?, ?)",
                ("id_old", "sender_d", old_ts),
            )
        # Ahora el nuevo mensaje debe pasar porque el último fue hace más de 10 s
        self.assertTrue(self.gate.claim("id_new", "sender_d"))


if __name__ == "__main__":
    unittest.main()
