import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dbagg.database.client import Database


class DatabaseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(SimpleNamespace(connection="synthetic"))
        self.conn = Mock()
        self.cursor = self.conn.cursor.return_value
        self.cursor.description = [("importe",)]
        self.connect = patch.object(self.db, "_connect", return_value=self.conn).start()
        self.addCleanup(patch.stopall)

    def test_parameters_precision_and_extra_row_detection(self):
        self.cursor.fetchmany.return_value = [(Decimal("123.4500"),), (Decimal("9.99"),)]
        answer = self.db.query("SELECT importe WHERE cliente=?", ("'danger'",), row_limit=1)
        self.cursor.execute.assert_called_once_with("SELECT importe WHERE cliente=?", "'danger'")
        self.cursor.fetchmany.assert_called_once_with(2)
        self.assertEqual(answer["rows"], [["123.4500"]])
        self.assertTrue(answer["has_more"])
        self.assertFalse(answer["values_may_be_truncated"])
        self.conn.close.assert_called_once()

    def test_cell_truncation_is_reported_only_when_it_happens(self):
        self.cursor.fetchmany.return_value = [("x" * 301,)]
        answer = self.db.query("SELECT dato")
        self.assertEqual(len(answer["rows"][0][0]), 300)
        self.assertTrue(answer["values_may_be_truncated"])
        self.assertFalse(answer["has_more"])

    def test_total_payload_truncation_is_disclosed(self):
        self.cursor.description = [("dato1",), ("dato2",)]
        self.cursor.fetchmany.return_value = [("x" * 300, "y" * 300)] * 50
        answer = self.db.query("SELECT dato1, dato2")
        self.assertTrue(answer["response_size_limit_reached"])
        self.assertLess(len(answer["rows"]), 50)

    def test_query_report_does_not_truncate_large_results(self):
        self.cursor.fetchmany.return_value = [("x" * 500,)] * 1500
        answer = self.db.query_report("SELECT dato", ("p",), row_limit=2001)
        self.cursor.execute.assert_called_once_with("SELECT dato", "p")
        self.cursor.fetchmany.assert_called_once_with(2002)
        self.assertEqual(len(answer["rows"]), 1500)
        self.assertEqual(len(answer["rows"][0][0]), 500)
        self.assertFalse(answer["has_more"])
        self.conn.close.assert_called_once()

    def test_query_report_detects_extra_rows(self):
        self.cursor.fetchmany.return_value = [(1,), (2,)]
        answer = self.db.query_report("SELECT dato", row_limit=1)
        self.assertEqual(answer["rows"], [["1"]])
        self.assertTrue(answer["has_more"])

    def test_query_report_rejects_a_limit_above_the_maximum(self):
        with self.assertRaises(ValueError):
            self.db.query_report("SELECT 1", row_limit=2002)
        self.cursor.execute.assert_not_called()

    def test_connection_is_closed_on_execute_failure(self):
        self.cursor.execute.side_effect = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            self.db.query("SELECT dato")
        self.conn.close.assert_called_once()

    def test_write_capable_login_is_refused_once_and_read_only_login_checked_once(self):
        patch.stopall()
        for roles, allowed in (((0, 0, 0, 0), True), ((0, 0, 1, 0), False), ((1, 0, 0, 0), False)):
            with (
                self.subTest(roles=roles),
                patch("dbagg.database.client.pyodbc.connect") as connect,
            ):
                conn = connect.return_value
                conn.cursor.return_value.execute.return_value.fetchone.return_value = roles
                db = Database(SimpleNamespace(connection="synthetic"))
                if allowed:
                    db._connect()
                    db._connect()
                    conn.cursor.return_value.execute.assert_called_once()
                else:
                    with self.assertRaises(PermissionError):
                        db._connect()
                    conn.close.assert_called_once()

    def test_invalid_limits_do_not_connect(self):
        for limit in (0, 51, True, 1.5):
            with self.assertRaises(ValueError):
                self.db.query("SELECT dato", row_limit=limit)
        self.connect.assert_not_called()
