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

    def test_connection_is_closed_on_execute_failure(self):
        self.cursor.execute.side_effect = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            self.db.query("SELECT dato")
        self.conn.close.assert_called_once()

    def test_invalid_limits_do_not_connect(self):
        for limit in (0, 51, True, 1.5):
            with self.assertRaises(ValueError):
                self.db.query("SELECT dato", row_limit=limit)
        self.connect.assert_not_called()
