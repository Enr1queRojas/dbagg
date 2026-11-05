"""Synthetic statements; SQLite exercises the queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date
from decimal import Decimal

import sqlglot

from dbagg.business.customers import CREDITS, CUSTOMERS, PAYMENTS, RETURNS
from dbagg.business.evidence import BusinessError
from dbagg.business.parties import CUSTOMER, SUPPLIER
from dbagg.business.statements import (
    MAX_STATEMENT_ROWS,
    STATEMENT_OPERATION,
    StatementQueries,
    StatementTooLarge,
)
from dbagg.business.suppliers import PURCHASES, SUPPLIER_PAYMENTS, SUPPLIERS

TEXT = "nvarchar"
NUMBER = "decimal"
LINES = {
    "ID": "bigint",
    "FOLIO": "bigint",
    "FECHA": "date",
    "CANTIDAD": NUMBER,
    "CODIGO": TEXT,
    "CONCEPTO": TEXT,
    "PRECIO": NUMBER,
    "IMPORTE": NUMBER,
    "TOTAL": NUMBER,
    "ESTADO": TEXT,
}
MONEY = {
    "ID": "bigint",
    "FOLIO": "bigint",
    "FECHA": "date",
    "BANCO": TEXT,
    "IMPORTE": NUMBER,
    "ESTADO": TEXT,
}
CATALOG = {"CODIGO": TEXT, "NOMBRE": TEXT, "SALDO ACTUAL": NUMBER, "SALDO_INICIAL": NUMBER}
DEFINITIONS = {
    CUSTOMERS: CATALOG,
    SUPPLIERS: {**CATALOG, "ESTADO": TEXT},
    CREDITS: {**LINES, "CLIENTE": TEXT},
    RETURNS: {**LINES, "CLIENTE": TEXT},
    PAYMENTS: {**MONEY, "CLIENTE": TEXT},
    PURCHASES: {**LINES, "PROVEEDOR": TEXT},
    SUPPLIER_PAYMENTS: {**MONEY, "PROVEEDOR": TEXT},
}
PERIOD = (date(2026, 8, 30), date(2026, 11, 4))


class StatementDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        self.statements = []
        for table, columns in DEFINITIONS.items():
            names = ", ".join(
                f'"{c}" NUMERIC' if kind in {NUMBER, "bigint"} else f'"{c}"'
                for c, kind in columns.items()
            )
            self.conn.execute(f"CREATE TABLE {table} ({names})")

    def schema(self, sources):
        return json.dumps(
            [
                {
                    "table": table,
                    "columns": [{"name": n, "type": t} for n, t in DEFINITIONS[table].items()],
                }
                for table in sources
            ]
        )

    def query_report(self, sql, params=(), row_limit=2001):
        self.statements.append(sql)
        rendered = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
        params = tuple(p.isoformat() if isinstance(p, date) else p for p in params)
        cursor = self.conn.execute(rendered, params)
        rows = cursor.fetchmany(row_limit + 1)
        return {
            "columns": [c[0] for c in cursor.description],
            "rows": [[None if v is None else str(v) for v in row] for row in rows[:row_limit]],
            "has_more": len(rows) > row_limit,
        }

    query = query_report

    def insert(self, table, **values):
        columns = ", ".join(f'"{c}"' for c in values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values())
        )

    def customer(self, code, balance, opening="0", name="Cliente"):
        self.insert(
            CUSTOMERS,
            CODIGO=code,
            NOMBRE=name,
            **{"SALDO ACTUAL": balance},
            SALDO_INICIAL=opening,
        )

    def supplier(self, code, balance, opening="0", name="Proveedor", state="ACTIVO"):
        self.insert(
            SUPPLIERS,
            CODIGO=code,
            NOMBRE=name,
            ESTADO=state,
            **{"SALDO ACTUAL": balance},
            SALDO_INICIAL=opening,
        )

    def line(
        self, table, party, id, fecha, importe, total=None, folio=None, state="ACTIVA", **more
    ):
        column = "PROVEEDOR" if table == PURCHASES else "CLIENTE"
        self.insert(
            table,
            ID=id,
            FOLIO=folio or id,
            FECHA=fecha,
            CANTIDAD=more.get("cantidad", 1),
            CODIGO=more.get("codigo", "MAIZ"),
            CONCEPTO=more.get("concepto", "N/A"),
            PRECIO=more.get("precio", 1),
            IMPORTE=importe,
            TOTAL=importe if total is None else total,
            ESTADO=state,
            **{column: party},
        )

    def money(self, table, party, id, fecha, importe, state="ACTIVA", banco="SANTANDER"):
        column = "PROVEEDOR" if table == SUPPLIER_PAYMENTS else "CLIENTE"
        self.insert(
            table,
            ID=id,
            FOLIO=id,
            FECHA=fecha,
            BANCO=banco,
            IMPORTE=importe,
            ESTADO=state,
            **{column: party},
        )


class StatementCase(unittest.TestCase):
    def setUp(self):
        self.db = StatementDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = StatementQueries(self.db, set(DEFINITIONS), today=date(2026, 11, 4))
        # The ERP example of A.ESPARZA3.
        self.db.customer("A.ESPARZA3", "59850.60", "71888.60", "ALEJANDRO ESPARZA")
        self.db.line(CREDITS, "A.ESPARZA3", 1, "2026-01-10", "1503406.60")
        self.db.money(PAYMENTS, "A.ESPARZA3", 2, "2026-02-10", "1525809.68")
        self.db.line(
            CREDITS, "A.ESPARZA3", 13440, "2026-09-29", "58415.40", precio="8.30", cantidad=7038
        )
        self.db.money(PAYMENTS, "A.ESPARZA3", 13943, "2026-10-01", "48050.32")

    def build(self, entity, code, start=PERIOD[0], end=PERIOD[1]):
        return self.queries.build(entity, code, start, end)


class CustomerStatementTests(StatementCase):
    def test_customer_reproduces_the_erp_example(self):
        s = self.build(CUSTOMER, "A.ESPARZA3")
        self.assertEqual(s.opening, Decimal("49485.52"))
        self.assertEqual([r.saldo for r in s.rows], [Decimal("107900.92"), Decimal("59850.60")])
        self.assertEqual(s.closing, Decimal("59850.60"))
        self.assertEqual(s.current, Decimal("59850.60"))
        self.assertEqual(s.warnings, ())
        self.assertEqual((s.name, s.code, s.entity), ("ALEJANDRO ESPARZA", "A.ESPARZA3", CUSTOMER))

    def test_customer_rows_follow_the_erp_columns(self):
        credit, payment = self.build(CUSTOMER, "A.ESPARZA3").rows
        self.assertEqual(
            (credit.kind, credit.fecha, credit.folio, credit.cantidad, credit.codigo),
            ("CRÉDITO", date(2026, 9, 29), "13440", Decimal("7038"), "MAIZ"),
        )
        self.assertEqual((credit.precio, credit.total), (Decimal("8.30"), Decimal("58415.40")))
        self.assertEqual(
            (payment.kind, payment.codigo, payment.concepto, payment.abono, payment.total),
            ("COBRANZA", "SANTANDER", "ABONO", Decimal("48050.32"), Decimal("0")),
        )

    def test_returns_reduce_the_balance(self):
        self.db.customer("DEV", "70", "0")
        self.db.line(CREDITS, "DEV", 1, "2026-09-01", "100")
        self.db.line(RETURNS, "DEV", 2, "2026-09-02", "30", total="999")
        s = self.build(CUSTOMER, "DEV")
        self.assertEqual(s.rows[-1].kind, "DEVOLUCIÓN")
        self.assertEqual(s.rows[-1].devolucion, Decimal("30"))
        self.assertEqual(s.rows[-1].total, Decimal("0"))
        self.assertEqual(s.closing, Decimal("70"))
        self.assertEqual(s.warnings, ())

    def test_cash_sales_are_never_queried(self):
        self.build(CUSTOMER, "A.ESPARZA3")
        self.assertFalse(any("contado" in sql.lower() for sql in self.db.statements))

    def test_resolve_finds_a_customer(self):
        self.assertEqual(self.queries.resolve(CUSTOMER, "ESPARZA")["codigo"], "A.ESPARZA3")


class SupplierStatementTests(StatementCase):
    def setUp(self):
        super().setUp()
        self.db.supplier("ACME", "150", "0", "Acme SA")
        self.db.line(PURCHASES, "ACME", 1, "2026-10-01", "100", total="999", folio=7)
        self.db.line(PURCHASES, "ACME", 2, "2026-10-01", "50", total="888", folio=7)

    def test_supplier_uses_importe_never_total(self):
        s = self.build(SUPPLIER, "ACME", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(s.rows[-1].saldo, Decimal("150"))
        self.assertEqual({r.kind for r in s.rows}, {"COMPRA"})
        self.assertEqual(s.rows[0].total, Decimal("100"))
        self.assertEqual(s.warnings, ())

    def test_cancelled_rows_are_excluded(self):
        self.db.line(PURCHASES, "ACME", 3, "2026-09-01", "5000", state="CANCELADA")
        self.db.line(PURCHASES, "ACME", 4, "2026-10-02", "6000", state="CANCELADA")
        s = self.build(SUPPLIER, "ACME", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(s.opening, Decimal("0"))
        self.assertEqual(s.closing, Decimal("150"))

    def test_same_day_charges_come_before_credits(self):
        self.db.money(SUPPLIER_PAYMENTS, "ACME", 1, "2026-10-01", "150")
        self.db.conn.execute(f"UPDATE {SUPPLIERS} SET [SALDO ACTUAL] = 0 WHERE CODIGO = 'ACME'")
        s = self.build(SUPPLIER, "ACME", date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual([r.kind for r in s.rows], ["COMPRA", "COMPRA", "PAGO"])
        self.assertEqual(s.rows[-1].abono, Decimal("150"))
        self.assertEqual(s.rows[-1].codigo, "SANTANDER")

    def test_end_date_is_inclusive(self):
        s = self.build(SUPPLIER, "ACME", date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(len(s.rows), 2)

    def test_period_without_rows_keeps_opening_balance(self):
        s = self.build(SUPPLIER, "ACME", date(2020, 1, 1), date(2020, 1, 31))
        self.assertEqual((s.rows, s.closing), ((), s.opening))
        self.assertEqual(s.opening, Decimal("0"))

    def test_old_period_is_not_a_mismatch(self):
        s = self.build(SUPPLIER, "ACME", date(2026, 9, 1), date(2026, 9, 30))
        self.assertNotEqual(s.closing, s.current)
        self.assertEqual(s.warnings, ())

    def test_opening_includes_earlier_movements(self):
        s = self.build(SUPPLIER, "ACME", date(2026, 10, 2), date(2026, 10, 31))
        self.assertEqual(s.opening, Decimal("150"))

    def test_mismatch_is_reported(self):
        self.db.supplier("DESC", "999")
        self.db.line(PURCHASES, "DESC", 1, "2026-10-01", "100")
        self.assertIn("no cuadran", self.build(SUPPLIER, "DESC").warnings[0])

    def test_undated_rows_are_counted_and_reported(self):
        self.db.supplier("SINF", "100")
        self.db.line(PURCHASES, "SINF", 1, None, "100")
        warnings = self.build(SUPPLIER, "SINF").warnings
        self.assertEqual(len(warnings), 1)
        self.assertIn("1 movimiento", warnings[0])
        self.assertIn("sin fecha válida", warnings[0])

    def test_unknown_current_balance_is_reported(self):
        self.db.supplier("NULO", None)
        s = self.build(SUPPLIER, "NULO")
        self.assertIsNone(s.current)
        self.assertIn("desconocido", s.warnings[0])

    def test_more_than_the_limit_is_refused(self):
        self.db.supplier("MUCHOS", "0")
        for i in range(MAX_STATEMENT_ROWS + 1):
            self.db.line(PURCHASES, "MUCHOS", i, "2026-10-01", "1")
        with self.assertRaises(StatementTooLarge):
            self.build(SUPPLIER, "MUCHOS")

    def test_exactly_the_limit_is_accepted_across_tables(self):
        self.db.supplier("JUSTO", "0")
        for i in range(MAX_STATEMENT_ROWS - 1):
            self.db.line(PURCHASES, "JUSTO", i, "2026-10-01", "1")
        self.db.money(SUPPLIER_PAYMENTS, "JUSTO", 1, "2026-10-01", str(MAX_STATEMENT_ROWS - 1))
        self.assertEqual(len(self.build(SUPPLIER, "JUSTO").rows), MAX_STATEMENT_ROWS)
        self.db.money(SUPPLIER_PAYMENTS, "JUSTO", 2, "2026-10-01", "0")
        with self.assertRaises(StatementTooLarge):
            self.build(SUPPLIER, "JUSTO")

    def test_inverted_dates_are_rejected(self):
        with self.assertRaisesRegex(BusinessError, "período"):
            self.build(SUPPLIER, "ACME", date(2026, 10, 2), date(2026, 10, 1))

    def test_inactive_supplier_is_marked(self):
        self.db.supplier("VIEJO", "0", state="INACTIVO")
        self.assertTrue(self.build(SUPPLIER, "VIEJO").inactive)

    def test_resolve_lists_homonyms_with_the_existing_wording(self):
        self.db.supplier("C1", "1", name="El Cuervo")
        self.db.supplier("C2", "1", name="Cuervo Dos")
        answer = self.queries.resolve(SUPPLIER, "Cuervo")
        self.assertIn("Encontré varios proveedores:", answer.text)
        self.assertEqual(answer.operation, STATEMENT_OPERATION)

    def test_unauthorized_source_is_rejected(self):
        queries = StatementQueries(self.db, {SUPPLIERS}, today=date(2026, 11, 4))
        with self.assertRaisesRegex(BusinessError, "autorizada"):
            queries.build(SUPPLIER, "ACME", *PERIOD)


if __name__ == "__main__":
    unittest.main()
