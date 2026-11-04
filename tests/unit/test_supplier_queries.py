"""Synthetic supplier scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date

import sqlglot

from dbagg.business.evidence import BusinessError
from dbagg.business.suppliers import (
    PURCHASES,
    SUPPLIER_PAYMENTS,
    SUPPLIERS,
    SupplierQueries,
    validate_supplier_context,
)
from dbagg.context.loader import load_business_context
from dbagg.database.catalog import load_catalog

TODAY = date(2026, 10, 8)
DEFINITIONS = {
    SUPPLIERS: {
        "CODIGO": "nvarchar",
        "NOMBRE": "nvarchar",
        "SALDO ACTUAL": "decimal",
        "SALDO_INICIAL": "decimal",
        "ESTADO": "nvarchar",
    },
    SUPPLIER_PAYMENTS: {
        "ID": "bigint",
        "FOLIO": "bigint",
        "PROVEEDOR": "nvarchar",
        "FECHA": "date",
        "IMPORTE": "decimal",
        "ESTADO": "nvarchar",
    },
    PURCHASES: {
        "ID": "bigint",
        "FOLIO": "bigint",
        "PROVEEDOR": "nvarchar",
        "FECHA": "date",
        "CANTIDAD": "decimal",
        "CONCEPTO": "nvarchar",
        "PRECIO": "decimal",
        "IMPORTE": "decimal",
        "TOTAL": "decimal",
        "ESTADO": "nvarchar",
    },
}


class SupplierDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        for table, columns in DEFINITIONS.items():
            names = ", ".join(
                f'"{c}" NUMERIC' if kind == "decimal" else f'"{c}"' for c, kind in columns.items()
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

    def supplier(self, code, name, balance="0", opening="0", state="ACTIVO"):
        self.conn.execute(
            f"INSERT INTO {SUPPLIERS} VALUES (?, ?, ?, ?, ?)", (code, name, balance, opening, state)
        )

    def payment(self, id, supplier, fecha, value, state="ACTIVA"):
        self.conn.execute(
            f"INSERT INTO {SUPPLIER_PAYMENTS} VALUES (?, ?, ?, ?, ?, ?)",
            (id, id, supplier, fecha, value, state),
        )

    def purchase(
        self, id, folio, supplier, fecha, value, total=None, state="ACTIVA", concept="Maíz"
    ):
        self.conn.execute(
            f"INSERT INTO {PURCHASES} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id, folio, supplier, fecha, 10, concept, 2, value, total or value, state),
        )


class SupplierCase(unittest.TestCase):
    def setUp(self):
        self.db = SupplierDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = SupplierQueries(
            self.db, {SUPPLIERS, SUPPLIER_PAYMENTS, PURCHASES}, today=TODAY
        )
        self.db.supplier("ACME", "Acme SA", "1500")

    def run_op(self, operation, supplier=None, **kwargs):
        return self.queries.execute(operation, supplier=supplier, **kwargs)


class SupplierBalanceTests(SupplierCase):
    def test_positive_balance_says_we_owe(self):
        text = self.run_op("saldo", "ACME").text
        self.assertEqual(text.splitlines()[0], "Proveedor Acme SA (código ACME)")
        self.assertIn("Le debemos: 1,500.00", text)

    def test_negative_balance_is_in_our_favor(self):
        self.db.supplier("FAVOR", "Favor SA", "-200")
        text = self.run_op("saldo", "FAVOR").text
        self.assertIn("Saldo a nuestro favor: 200.00", text)
        self.assertNotIn("Le debemos", text)

    def test_zero_balance_has_nothing_pending(self):
        self.db.supplier("CERO", "Cero SA", "0")
        self.assertIn("Sin saldo pendiente", self.run_op("saldo", "CERO").text)

    def test_unknown_balance_is_unavailable(self):
        self.db.supplier("NULO", "Nulo SA", None)
        self.assertEqual(self.run_op("saldo", "NULO").status, "unavailable")

    def test_inactive_supplier_is_marked(self):
        self.db.supplier("VIEJO", "Viejo SA", "5", state="INACTIVO")
        self.assertIn("Proveedor inactivo en el catálogo.", self.run_op("saldo", "VIEJO").text)

    def test_several_matches_ask_and_mark_inactive(self):
        self.db.supplier("CUE1", "El Cuervo", "5")
        self.db.supplier("CUE2", "Cuervo Viejo", "9", state="INACTIVO")
        answer = self.run_op("saldo", "Cuervo")
        self.assertEqual(answer.status, "needs_clarification")
        self.assertTrue(answer.text.startswith("Encontré varios proveedores:"))
        self.assertIn("(código CUE2) · inactivo", answer.text)
        self.assertNotIn("9.00", answer.text)

    def test_unknown_name_needs_clarification(self):
        self.assertEqual(self.run_op("saldo", "Nadie").status, "needs_clarification")

    def test_ranking_lists_only_positive_balances(self):
        self.db.supplier("FAVOR", "Favor SA", "-200")
        text = self.run_op("ranking_deuda", limit=5).text
        self.assertTrue(text.startswith("Proveedores a los que más les debemos"))
        self.assertIn("ACME", text)
        self.assertNotIn("FAVOR", text)

    def test_duplicate_code_is_rejected(self):
        self.db.supplier("DOBLE", "Doble A", "1")
        self.db.supplier("DOBLE", "Doble B", "2")
        with self.assertRaisesRegex(BusinessError, "repite una clave"):
            self.run_op("saldo", "DOBLE")

    def test_matching_balances_are_listed_without_summing(self):
        self.db.supplier("CUE1", "El Cuervo", "5")
        self.db.supplier("CUE2", "Cuervo Dos", "-7")
        text = self.run_op("saldos_coincidencias", "Cuervo").text
        self.assertIn("le debemos 5.00", text)
        self.assertIn("a nuestro favor 7.00", text)
        self.assertIn("no se suman", text)

    def test_count_matches_shows_no_balances(self):
        self.db.supplier("CUE1", "El Cuervo", "5")
        text = self.run_op("contar_coincidencias", "Cuervo").text
        self.assertIn("1 proveedor", text)
        self.assertNotIn("5.00", text)

    def test_like_wildcards_are_literal(self):
        self.assertIn("No encontré", self.run_op("saldo", "%").text)


class SupplierContractTests(unittest.TestCase):
    def test_supplier_sources_are_business_tool(self):
        catalog = load_catalog()
        for source in (SUPPLIERS, SUPPLIER_PAYMENTS):
            self.assertEqual(catalog.sources[source].access, "business_tool")

    def test_purchases_stay_exploratory(self):
        self.assertEqual(load_catalog().sources[PURCHASES].access, "exploratory")

    def test_default_context_matches_supplier_contract(self):
        validate_supplier_context(load_business_context())

    def test_changed_supplier_column_is_rejected(self):
        context = json.loads(load_business_context())
        context["topics"]["supplier_balance"]["columns"]["current_balance"] = "IMPORTE"
        with self.assertRaisesRegex(BusinessError, "contrato"):
            validate_supplier_context(json.dumps(context))

    def test_changed_payment_filter_is_rejected(self):
        context = json.loads(load_business_context())
        context["topics"]["supplier_payments"]["confirmed_filters"] = {}
        with self.assertRaisesRegex(BusinessError, "contrato"):
            validate_supplier_context(json.dumps(context))


if __name__ == "__main__":
    unittest.main()
