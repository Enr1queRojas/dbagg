"""Synthetic stock and sales scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date

import sqlglot

from dbagg.business.evidence import BusinessError
from dbagg.business.products import (
    CASH,
    CREDITS,
    RETURNS,
    STOCK,
    ProductQueries,
    comparison_periods,
)

TODAY = date(2026, 10, 28)
SALE_COLUMNS = {
    "CODIGO": "nvarchar",
    "CONCEPTO": "nvarchar",
    "CANTIDAD": "decimal",
    "IMPORTE": "decimal",
    "ESTADO": "nvarchar",
    "FECHA": "date",
}


class SyntheticDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        self.conn.execute(
            f"CREATE TABLE {STOCK} (CODIGO TEXT, DESCRIPCION TEXT, EXISTENCIAS NUMERIC)"
        )
        for table in (CREDITS, CASH, RETURNS):
            self.conn.execute(
                f"CREATE TABLE {table} (CODIGO TEXT, CONCEPTO TEXT, CANTIDAD NUMERIC, "
                "IMPORTE NUMERIC, ESTADO TEXT, FECHA TEXT)"
            )

    def schema(self, sources):
        definitions = {
            STOCK: {"CODIGO": "nvarchar", "DESCRIPCION": "nvarchar", "EXISTENCIAS": "decimal"},
            CREDITS: SALE_COLUMNS,
            CASH: SALE_COLUMNS,
            RETURNS: SALE_COLUMNS,
        }
        return json.dumps(
            [
                {"table": t, "columns": [{"name": n, "type": v} for n, v in cols.items()]}
                for t, cols in definitions.items()
                if t in sources
            ]
        )

    def query(self, sql, params=(), row_limit=50):
        rendered = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
        params = tuple(p.isoformat() if isinstance(p, date) else p for p in params)
        cursor = self.conn.execute(rendered, params)
        rows = cursor.fetchmany(row_limit + 1)
        return {
            "columns": [c[0] for c in cursor.description],
            "rows": [[None if v is None else str(v) for v in row] for row in rows[:row_limit]],
            "has_more": len(rows) > row_limit,
        }

    def stock(self, code, description, quantity):
        self.conn.execute(f"INSERT INTO {STOCK} VALUES (?, ?, ?)", (code, description, quantity))

    def sale(self, table, code, concept, quantity, importe, fecha, estado="ACTIVA"):
        self.conn.execute(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)",
            (code, concept, quantity, importe, estado, fecha),
        )


class ProductQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = SyntheticDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = ProductQueries(self.db, {STOCK, CREDITS, CASH, RETURNS}, today=TODAY)

    def table(self, text):
        return [line.split() for line in text.split("```")[1].strip("\n").splitlines()[2:]]

    def test_stock_converts_each_product_with_reviewed_units(self):
        self.db.stock("MAIZ", "MAIZ BLANCO", 3000)
        self.db.stock("AMARILLO", "MAIZ AMARILLO", 500)  # ERP says BULTO; reviewed as kg
        self.db.stock("MAIZ50", "MAIZ MTY 50 KG", 20)
        self.db.stock("MAIZ NUEVO", "MAIZ NUEVO", 7)
        self.db.stock("SEMILLA MAIZ", "ASGROW", -16)
        self.db.stock("REYNA", "FRIJOL REYNA", 900)
        answer = self.queries.execute("existencias", producto="maíz")
        rows = self.table(answer.text)
        self.assertIn(["MAIZ", "BLANCO", "3,000.00", "1", "3.00"], rows)
        self.assertIn(["MAIZ", "AMARILLO", "500.00", "1", "0.50"], rows)
        self.assertIn(["MAIZ", "MTY", "50", "KG", "20.00", "50", "1.00"], rows)
        self.assertIn(["MAIZ", "NUEVO", "7.00", "?", "sin", "dato"], rows)
        self.assertIn("Total: 4.50 t.", answer.text)
        self.assertIn("(falta en la tabla de unidades): MAIZ NUEVO.", answer.text)
        self.assertNotIn("ASGROW", answer.text)
        self.assertNotIn("REYNA", answer.text)

    def test_stock_rejects_periods_customers_and_odd_product_text(self):
        for arguments in (
            {"period": "este_mes"},
            {"customer": "Robles"},
            {"producto": "maíz'; DROP"},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(BusinessError):
                self.queries.execute("existencias", **arguments)

    def test_sales_formula_excludes_loans_and_compares_with_previous_month(self):
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 10000, 80000, "2026-10-05")
        self.db.sale(CASH, "BILLZ45", "FRIJOL BILLZ 45 KG", 20, 14000, "2026-10-06")
        self.db.sale(CASH, "A", "PRÉSTAMO", 1000000, 1000000, "2026-10-07")
        self.db.sale(CASH, "UREA", "FERTILIZANTE", 500, 7000, "2026-10-07")
        self.db.sale(RETURNS, "MAIZ", "MAIZ BLANCO", 1000, 8000, "2026-10-08")
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 5000, 40000, "2026-09-10")
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 5000, 40000, "2026-09-30")
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 99999, 99999, "2026-10-09", "CANCELADA")
        answer = self.queries.execute("ventas")
        rows = self.table(answer.text)
        # Pesos: 80,000 + 14,000 + 7,000 − 8,000. Grain kg: 10,000 + 20 × 45 − 1,000.
        self.assertEqual(rows[0], ["actual", "01/10/2026-28/10/2026", "93,000.00", "9.90"])
        self.assertEqual(rows[1][-3:], ["01/09/2026-28/09/2026", "40,000.00", "5.00"])
        self.assertEqual(rows[2][-3:], ["01/09/2026-30/09/2026", "80,000.00", "10.00"])
        self.assertIn("+132.5 % en pesos, +98.0 % en toneladas", answer.text)
        self.assertEqual(answer.operation, "ventas")

    def test_sales_can_be_filtered_by_product_word(self):
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 10000, 80000, "2026-10-05")
        self.db.sale(CREDITS, "REYNA", "FRIJOL REYNA", 1000, 25000, "2026-10-05")
        self.db.sale(CREDITS, "SALT 25", "SALTILLO25", 40, 16000, "2026-10-06")
        answer = self.queries.execute("ventas", producto="frijoles", period="este_mes")
        # SALTILLO has no «frijol» in its name; the reviewed family still includes it.
        self.assertEqual(self.table(answer.text)[0][-2:], ["41,000.00", "2.00"])
        self.assertIn("Ventas de frijol", answer.text)

    def test_comparison_periods(self):
        self.assertEqual(
            comparison_periods("este_mes", date(2026, 3, 31))[1][1:],
            (date(2026, 2, 1), date(2026, 3, 1)),
        )
        self.assertEqual(
            comparison_periods("mes_pasado", TODAY),
            [
                ("actual", date(2026, 9, 1), date(2026, 10, 1)),
                ("mes anterior", date(2026, 8, 1), date(2026, 9, 1)),
            ],
        )
        self.assertEqual(
            comparison_periods("rango", TODAY, "2026-10-01", "2026-10-10")[1],
            ("periodo anterior", date(2026, 9, 21), date(2026, 10, 1)),
        )
        # A whole calendar month is compared with the previous month, not the last 31 days.
        self.assertEqual(
            comparison_periods("rango", TODAY, "2025-10-01", "2025-10-31")[1],
            ("mes anterior", date(2025, 9, 1), date(2025, 10, 1)),
        )
        with self.assertRaises(BusinessError):
            comparison_periods("rango", TODAY, "2026-11-01", "2026-11-30")

    def test_year_over_year_cuts_the_month_in_progress_at_today(self):
        expected = [
            ("actual", date(2026, 10, 1), date(2026, 10, 29)),
            ("mismo periodo año anterior", date(2025, 10, 1), date(2025, 10, 29)),
            ("mes completo año anterior", date(2025, 10, 1), date(2025, 11, 1)),
        ]
        self.assertEqual(comparison_periods("este_mes", TODAY, compare="anio_anterior"), expected)
        self.assertEqual(
            comparison_periods("rango", TODAY, "2026-10-01", "2026-10-31", "anio_anterior"),
            expected,
        )
        self.assertEqual(
            comparison_periods("rango", TODAY, "2025-09-01", "2025-09-30", "anio_anterior")[1],
            ("mismo periodo año anterior", date(2024, 9, 1), date(2024, 10, 1)),
        )
        with self.assertRaises(BusinessError):
            comparison_periods("este_mes", TODAY, compare="semana")

    def test_year_over_year_sales(self):
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 10000, 80000, "2026-10-05")
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 4000, 40000, "2025-10-05")
        self.db.sale(CREDITS, "MAIZ", "MAIZ BLANCO", 1000, 10000, "2025-10-30")
        answer = self.queries.execute("ventas", comparar="anio_anterior")
        rows = self.table(answer.text)
        self.assertEqual(rows[1][-3:], ["01/10/2025-28/10/2025", "40,000.00", "4.00"])
        self.assertEqual(rows[2][-3:], ["01/10/2025-31/10/2025", "50,000.00", "5.00"])
        self.assertIn("Variación contra mismo periodo año anterior: +100.0 % en pesos", answer.text)
        with self.assertRaises(BusinessError):
            self.queries.execute("existencias", comparar="anio_anterior")


if __name__ == "__main__":
    unittest.main()
