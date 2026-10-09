"""Synthetic business scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date
from unittest.mock import Mock

import sqlglot

from dbagg.business.calendar import period_bounds
from dbagg.business.customers import CUSTOMERS, PAYMENTS, CustomerQueries, validate_context
from dbagg.business.evidence import BusinessError, amount, records, render_exploratory
from dbagg.context.loader import load_business_context

TODAY = date(2026, 10, 8)


def catalogue(date_type="date"):
    definitions = {
        CUSTOMERS: {"CODIGO": "nvarchar", "NOMBRE": "nvarchar", "SALDO ACTUAL": "decimal"},
        PAYMENTS: {
            "CLIENTE": "nvarchar",
            "ESTADO": "nvarchar",
            "FECHA": date_type,
            "IMPORTE": "decimal",
            "ID": "int",
            "FOLIO": "nvarchar",
        },
    }
    return [
        {"table": t, "columns": [{"name": n, "type": v} for n, v in cols.items()]}
        for t, cols in definitions.items()
    ]


def result(columns, rows, more=False):
    return {
        "columns": columns,
        "rows": [[None if v is None else str(v) for v in row] for row in rows],
        "has_more": more,
    }


class SyntheticDatabase:
    def __init__(self, check_same_thread=True):
        self.conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        self.conn.execute(
            f'CREATE TABLE {CUSTOMERS} (CODIGO TEXT, NOMBRE TEXT, "SALDO ACTUAL" NUMERIC)'
        )
        self.conn.execute(
            f"CREATE TABLE {PAYMENTS} (ID INTEGER, FOLIO TEXT, CLIENTE TEXT, FECHA TEXT, IMPORTE NUMERIC, ESTADO TEXT)"
        )
        self.calls = []

    def schema(self, sources):
        return json.dumps([entry for entry in catalogue() if entry["table"] in sources])

    def query(self, sql, params=(), row_limit=50):
        self.calls.append((sql, params, row_limit))
        rendered = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
        # Explicit conversion avoids sqlite's deprecated date adapter.
        params = tuple(p.isoformat() if isinstance(p, date) else p for p in params)
        cursor = self.conn.execute(rendered, params)
        rows = cursor.fetchmany(row_limit + 1)
        return result([c[0] for c in cursor.description], rows[:row_limit], len(rows) > row_limit)

    def customer(self, code="C1", name="Ana Demo", balance="123.45"):
        self.conn.execute(f"INSERT INTO {CUSTOMERS} VALUES (?, ?, ?)", (code, name, balance))

    def payment(self, id, fecha, amount, estado="ACTIVA", customer="C1"):
        self.conn.execute(
            f"INSERT INTO {PAYMENTS} VALUES (?, ?, ?, ?, ?, ?)",
            (id, f"F{id}", customer, fecha, amount, estado),
        )


class CustomerQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = CustomerQueries(self.db, {CUSTOMERS, PAYMENTS}, today=TODAY)
        self.db.customer()

    def ask(self, operation="saldo", customer="Ana", **kwargs):
        return self.queries.execute(operation, customer=customer, **kwargs)

    def test_balance_uses_actual_value_and_bound_customer_parameter(self):
        answer = self.ask()
        self.assertIn("123.45", answer.text)
        self.assertIn("Ana Demo (código C1)", answer.text)
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.sources, (CUSTOMERS,))
        self.assertTrue(answer.evidence_id)
        self.assertEqual(self.db.calls[0][1], ("Ana", "Ana"))
        self.assertNotIn("Ana", self.db.calls[0][0])

    def test_homonyms_require_selection_and_never_query_payments(self):
        self.db.customer("C2", "Ana Otra", "999")
        answer = self.ask("ultimo_pago")
        self.assertEqual(answer.status, "needs_clarification")
        self.assertIn("C1", answer.text)
        self.assertIn("C2", answer.text)
        self.assertNotIn("999", answer.text)
        self.assertTrue(all(PAYMENTS not in sql for sql, _, _ in self.db.calls))

    def test_exact_code_selects_one_customer_without_summing_homonyms(self):
        self.db.customer("C2", "Ana Otra", "999")
        self.assertIn("123.45", self.ask(customer="C1").text)

    def test_wildcards_and_sql_fragments_are_literals(self):
        for value in ("%", "_", "[", "' OR 1=1 --"):
            with self.subTest(value=value):
                answer = self.ask(customer=value)
                self.assertEqual(answer.status, "needs_clarification")
                self.assertNotIn("123.45", answer.text)

    def test_duplicate_code_under_another_name_blocks_balance(self):
        self.db.customer("C1", "Otra Persona", "999")
        with self.assertRaisesRegex(BusinessError, "único"):
            self.ask(customer="Ana Demo")

    def test_null_balance_is_unknown_not_zero(self):
        self.db.customer("C2", "Sin Saldo", None)
        with self.assertRaisesRegex(BusinessError, "importe"):
            self.ask(customer="C2")

    def test_zero_and_negative_balances_preserve_their_value(self):
        self.db.customer("C2", "Cero", "0")
        self.db.customer("C3", "Negativo", "-9.75")
        self.assertIn(": 0.00.", self.ask(customer="C2").text)
        self.assertIn(": -9.75.", self.ask(customer="C3").text)

    def test_ranking_keeps_limit_positive_balances_and_stable_ties(self):
        for code, value in (("C2", 200), ("C3", 200), ("C4", 0), ("C5", -10)):
            self.db.customer(code, "Demo " + code, value)
        answer = self.ask("ranking_deuda", customer=None, limit=2)
        self.assertIn("Demo C2", answer.text)
        self.assertIn("Demo C3", answer.text)
        self.assertNotIn("Ana Demo", answer.text)
        self.assertLess(answer.text.index("Demo C2"), answer.text.index("Demo C3"))
        self.assertIn("TOP 2", self.db.calls[-1][0])

    def test_ranking_discloses_unknown_balances_and_rejects_duplicates(self):
        self.db.customer("C2", "Sin Saldo", None)
        answer = self.ask("ranking_deuda", customer=None)
        self.assertEqual(answer.status, "partial")
        self.assertIn("saldo desconocido", answer.text)
        self.db.customer("C1", "Duplicado", 999)
        with self.assertRaisesRegex(BusinessError, "repite"):
            self.ask("ranking_deuda", customer=None)

    def test_week_total_filters_active_client_and_dates_but_keeps_negative_amounts(self):
        for id, fecha, value, state, code in (
            (1, "2026-10-05", 100, "ACTIVA", "C1"),
            (2, "2026-10-08 23:59:00", -10, "ACTIVA", "C1"),
            (3, "2026-10-04", 888, "ACTIVA", "C1"),
            (4, "2026-10-09", 888, "ACTIVA", "C1"),
            (5, "2026-10-07", 888, "CANCELADA", "C1"),
            (6, "2026-10-07", 888, "ACTIVA", "C2"),
        ):
            self.db.payment(id, fecha, value, state, code)
        answer = self.ask("total_pagos", period="esta_semana")
        self.assertIn(": 90.00.", answer.text)
        self.assertIn("05/10/2026 al 08/10/2026", answer.text)
        sql, params, _ = self.db.calls[-1]
        self.assertIn("SUM([IMPORTE])", sql)
        self.assertNotIn("TOP", sql)
        self.assertEqual(params, ("C1", date(2026, 10, 5), date(2026, 10, 9)))

    def test_latest_payment_preserves_ties_and_history_older_than_a_year(self):
        self.db.payment(1, "2023-01-25", 10)
        self.db.payment(2, "2023-01-25", 20)
        self.db.payment(3, "2023-01-24", 999)
        self.db.payment(4, "2026-10-08", 888, "CANCELADA")
        answer = self.ask("ultimo_pago")
        self.assertIn("10.00", answer.text)
        self.assertIn("20.00", answer.text)
        self.assertNotIn("999", answer.text)
        self.assertNotIn("888", answer.text)
        self.assertIn("ID y folio no prueban", answer.text)

    def test_native_timestamp_selects_latest_time_not_whole_day(self):
        self.db.payment(1, "2026-10-08 10:00:00", 10)
        self.db.payment(2, "2026-10-08 11:00:00", 20)
        answer = self.ask("ultimo_pago")
        self.assertIn("20.00", answer.text)
        self.assertNotIn("10.00 ·", answer.text)

    def test_missing_payment_date_prevents_latest_and_marks_total_partial(self):
        self.db.payment(1, None, 999)
        self.db.payment(2, "2026-10-08", 20)
        self.assertEqual(self.ask("ultimo_pago").status, "unavailable")
        answer = self.ask("total_pagos", period="esta_semana")
        self.assertEqual(answer.status, "partial")
        self.assertIn("20.00", answer.text)
        self.assertIn("sin fecha válida", answer.text)

    def test_missing_amount_blocks_total_and_is_shown_in_list(self):
        self.db.payment(1, "2026-10-08", None)
        self.assertEqual(self.ask("total_pagos").status, "unavailable")
        answer = self.ask("pagos")
        self.assertEqual(answer.status, "partial")
        self.assertIn("importe no disponible", answer.text)

    def test_empty_payments_do_not_claim_zero(self):
        answer = self.ask("total_pagos")
        self.assertEqual(answer.status, "empty")
        self.assertIn("No encontré pagos", answer.text)
        self.assertNotIn("0.00", answer.text)

    def test_extra_row_is_disclosed_and_not_summed(self):
        for id in range(7):
            self.db.payment(id, "2026-10-08", 10)
        answer = self.ask("pagos", limit=5)
        self.assertEqual(answer.status, "partial")
        self.assertIn("Muestro 5 registros", answer.text)
        self.assertEqual(answer.text.count(" · ID "), 5)

    def test_contract_permissions_and_types_fail_before_reading_rows(self):
        db = Mock()
        with self.assertRaisesRegex(BusinessError, "autorizada"):
            CustomerQueries(db, {CUSTOMERS}).execute("pagos", customer="Ana")
        db.schema.assert_not_called()
        broken = catalogue()
        broken[0]["columns"][-1]["type"] = "nvarchar"
        db.schema.return_value = json.dumps(broken)
        with self.assertRaisesRegex(BusinessError, "tipos"):
            CustomerQueries(db, {CUSTOMERS}).execute("saldo", customer="Ana")
        db.query.assert_not_called()

    def test_text_date_uses_day_month_conversion_and_blank_guard(self):
        db = Mock()
        db.schema.return_value = json.dumps(catalogue("nvarchar"))
        db.query.side_effect = [
            result(["codigo", "nombre"], [["C1", "Ana"]]),
            result(["codigo", "nombre", "importe"], [["C1", "Ana", 100]]),
            result(["invalid_dates"], [[0]]),
            result(["id", "folio", "fecha", "importe"], [[1, "F1", "2023-01-25", 20]]),
        ]
        answer = CustomerQueries(db, {CUSTOMERS, PAYMENTS}).execute("ultimo_pago", customer="C1")
        self.assertIn("20.00", answer.text)
        sql = db.query.call_args.args[0]
        self.assertIn("TRY_CONVERT(datetime2, NULLIF(LTRIM(RTRIM([FECHA])), ''), 103)", sql)
        self.assertNotIn("INSERTION_DATE", sql)
        self.assertNotIn("NOTE_DATE", sql)

    def test_invalid_period_or_limit_does_not_execute(self):
        for kwargs in (
            {"limit": True},
            {"limit": 11},
            {"period": "rango"},
            {"period": "rango", "start": "2026-10-08", "end": "2026-10-01"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(BusinessError):
                self.ask("pagos", **kwargs)
        self.assertEqual(self.db.calls, [])

    def test_context_override_cannot_silently_change_payment_date(self):
        context = json.loads(load_business_context())
        validate_context(json.dumps(context), "ultimo_pago")
        context["topics"]["collections"]["columns"]["date"] = "INSERTION_DATE"
        with self.assertRaisesRegex(BusinessError, "difiere"):
            validate_context(json.dumps(context), "ultimo_pago")


class EvidenceAndCalendarTests(unittest.TestCase):
    def test_calendar_boundaries_are_explicit_and_inclusive(self):
        self.assertEqual(
            period_bounds("esta_semana", TODAY), (date(2026, 10, 5), date(2026, 10, 9))
        )
        self.assertEqual(
            period_bounds("semana_pasada", TODAY), (date(2026, 9, 28), date(2026, 10, 5))
        )
        self.assertEqual(
            period_bounds("mes_pasado", date(2026, 1, 1)), (date(2025, 12, 1), date(2026, 1, 1))
        )
        self.assertEqual(
            period_bounds("rango", TODAY, "2024-02-29", "2024-02-29"),
            (date(2024, 2, 29), date(2024, 3, 1)),
        )

    def test_invalid_and_truncated_evidence_cannot_be_financial_answers(self):
        for payload in (
            {"columns": ["saldo"], "rows": [[100]], "values_may_be_truncated": True},
            {"columns": ["saldo"], "rows": [[100]], "response_size_limit_reached": True},
            {"columns": ["saldo", "saldo"], "rows": [[100, 200]]},
            {"columns": ["saldo"], "rows": [[100, 200]]},
        ):
            with self.subTest(payload=payload), self.assertRaises(BusinessError):
                records(payload)
        for value in (None, "nan", "Infinity", "sin datos"):
            with self.subTest(value=value), self.assertRaises(BusinessError):
                amount(value)

    def test_exploration_is_labelled_and_handles_null_or_empty_cells(self):
        text = render_exploratory(result(["dato", "saldo"], [["", None]]))
        self.assertIn("requiere revisión", text)
        self.assertIn("sin dato", text)
        self.assertIn("no sumar", text)
