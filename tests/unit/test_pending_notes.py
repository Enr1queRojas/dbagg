"""Synthetic FIFO allocation of payments and returns to credit notes (SQLite, not SQL Server)."""

import json
import unittest

from dbagg.business.customers import CREDITS, CUSTOMERS, PAYMENTS, RETURNS, CustomerQueries
from dbagg.business.evidence import BusinessError
from tests.unit.test_customer_queries import TODAY, SyntheticDatabase, catalogue


class CreditDatabase(SyntheticDatabase):
    def __init__(self):
        super().__init__()
        self.conn.execute(f"ALTER TABLE {CUSTOMERS} ADD COLUMN SALDO_INICIAL NUMERIC DEFAULT 0")
        self.conn.execute(
            f"CREATE TABLE {CREDITS} (CLIENTE TEXT, FOLIO TEXT, FECHA TEXT, TOTAL NUMERIC, ESTADO TEXT)"
        )
        self.conn.execute(f"CREATE TABLE {RETURNS} (CLIENTE TEXT, IMPORTE NUMERIC, ESTADO TEXT)")

    def schema(self, sources):
        entries = catalogue()
        for entry in entries:
            if entry["table"] == CUSTOMERS:
                entry["columns"].append({"name": "SALDO_INICIAL", "type": "decimal"})
        entries.append(
            {
                "table": CREDITS,
                "columns": [
                    {"name": n, "type": t}
                    for n, t in (
                        ("CLIENTE", "nvarchar"),
                        ("FOLIO", "bigint"),
                        ("FECHA", "date"),
                        ("TOTAL", "decimal"),
                        ("ESTADO", "nvarchar"),
                    )
                ],
            }
        )
        entries.append(
            {
                "table": RETURNS,
                "columns": [
                    {"name": "CLIENTE", "type": "nvarchar"},
                    {"name": "IMPORTE", "type": "decimal"},
                    {"name": "ESTADO", "type": "nvarchar"},
                ],
            }
        )
        return json.dumps([e for e in entries if e["table"] in sources])

    def account(self, balance, initial=0, code="D1"):
        self.conn.execute(
            f"INSERT INTO {CUSTOMERS} VALUES (?, ?, ?, ?)", (code, "Ana Demo", balance, initial)
        )

    def note(self, folio, fecha, total, estado="ACTIVA", code="D1"):
        self.conn.execute(
            f"INSERT INTO {CREDITS} VALUES (?, ?, ?, ?, ?)", (code, folio, fecha, total, estado)
        )

    def returned(self, amount, estado="ACTIVA", code="D1"):
        self.conn.execute(f"INSERT INTO {RETURNS} VALUES (?, ?, ?)", (code, amount, estado))


class PendingNotesTests(unittest.TestCase):
    def setUp(self):
        self.db = CreditDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = CustomerQueries(
            self.db, {CUSTOMERS, PAYMENTS, CREDITS, RETURNS}, today=TODAY
        )

    def ask(self):
        return self.queries.execute("notas_pendientes", customer="D1")

    def test_payments_settle_oldest_notes_first_and_the_rest_squares(self):
        # Notes 100 + 200 + 300 = 600; payments 150 + return 50 = 200 -> pending 400.
        self.db.account(400)
        self.db.note("1", "2026-08-01", 60)
        self.db.note("1", "2026-08-01", 40)  # two lines, one note of 100
        self.db.note("2", "2026-09-01", 200)
        self.db.note("3", "2026-10-01", 300)
        self.db.note("4", "2026-10-02", 999, estado="CANCELADA")
        self.db.payment(1, "2026-09-15", 150, customer="D1")
        self.db.payment(2, "2026-09-16", 500, estado="CANCELADA", customer="D1")
        self.db.returned(50)
        answer = self.ask()
        self.assertEqual(answer.status, "answered")
        self.assertNotIn("folio 1 ", answer.text)
        self.assertIn("folio 2 · nota 200.00 · pendiente 100.00 (parcial) · 37 días", answer.text)
        self.assertIn("folio 3 · nota 300.00 · pendiente 300.00 · 7 días", answer.text)
        self.assertNotIn("folio 4", answer.text)
        self.assertIn("Total pendiente: 400.00.", answer.text)
        self.assertIn("Cuadra con el saldo actual.", answer.text)
        self.assertIn("FIFO", answer.text)
        self.assertEqual(answer.sources, (CUSTOMERS, PAYMENTS, CREDITS, RETURNS))

    def test_opening_balance_is_the_oldest_debt(self):
        # Opening 300 + note 200 = 500; payments 100 -> opening 200 pending + note 200.
        self.db.account(400, initial=300)
        self.db.note("7", "2026-09-01", 200)
        self.db.payment(1, "2026-09-02", 100, customer="D1")
        answer = self.ask()
        self.assertIn("Saldo inicial pendiente: 200.00", answer.text)
        self.assertIn("folio 7 · nota 200.00 · pendiente 200.00", answer.text)
        self.assertIn("Cuadra con el saldo actual.", answer.text)

    def test_negative_opening_balance_acts_as_a_credit(self):
        self.db.account(150, initial=-50)
        self.db.note("1", "2026-09-01", 100)
        self.db.note("2", "2026-09-10", 100)
        answer = self.ask()
        self.assertIn("folio 1 · nota 100.00 · pendiente 50.00 (parcial)", answer.text)
        self.assertIn("folio 2 · nota 100.00 · pendiente 100.00", answer.text)
        self.assertNotIn("Saldo inicial pendiente", answer.text)

    def test_mismatch_with_current_balance_is_reported_not_hidden(self):
        self.db.account(999)
        self.db.note("1", "2026-09-01", 100)
        answer = self.ask()
        self.assertEqual(answer.status, "partial")
        self.assertIn("no cuadran con el saldo actual", answer.text)
        self.assertNotIn("Cuadra con el saldo actual.", answer.text)

    def test_zero_or_credit_balance_has_no_pending_notes(self):
        self.db.account(-25)
        self.db.note("1", "2026-09-01", 100)
        self.db.payment(1, "2026-09-02", 125, customer="D1")
        answer = self.ask()
        self.assertIn("no tiene notas de crédito pendientes", answer.text)
        self.assertIn("a favor", answer.text)

    def test_dates_are_rejected_and_unknown_balance_is_not_zero(self):
        self.db.account(None)
        with self.assertRaisesRegex(BusinessError, "desconocido"):
            self.ask()
        with self.assertRaises(BusinessError):
            self.queries.execute("notas_pendientes", customer="D1", period="este_mes")


if __name__ == "__main__":
    unittest.main()
