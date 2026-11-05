"""Synthetic supplier scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date

import sqlglot

from dbagg.agent.intent import listed_codes
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
    def __init__(self, check_same_thread=True):
        self.conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        for table, columns in DEFINITIONS.items():
            names = ", ".join(
                f'"{c}" NUMERIC' if kind in {"decimal", "bigint"} else f'"{c}"'
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


class SupplierPaymentTests(SupplierCase):
    def test_total_payments_excludes_cancelled(self):
        self.db.payment(1, "ACME", "2026-10-02", "100")
        self.db.payment(2, "ACME", "2026-10-03", "200")
        self.db.payment(3, "ACME", "2026-10-04", "999", state="CANCELADA")
        text = self.run_op("total_pagos", "ACME", period="este_mes").text
        self.assertTrue(text.startswith("Proveedor Acme SA (código ACME)"))
        self.assertIn("Total pagado: 300.00", text)

    def test_total_payments_respects_the_period(self):
        self.db.payment(1, "ACME", "2026-09-30", "100")
        answer = self.run_op("total_pagos", "ACME", period="este_mes")
        self.assertEqual(answer.status, "empty")

    def test_last_payment_with_invalid_date_is_unavailable(self):
        self.db.payment(1, "ACME", None, "100")
        self.assertEqual(self.run_op("ultimo_pago", "ACME").status, "unavailable")

    def test_last_payment_shows_only_the_latest_date(self):
        self.db.payment(1, "ACME", "2026-09-01", "100")
        self.db.payment(2, "ACME", "2026-10-01", "250")
        text = self.run_op("ultimo_pago", "ACME").text
        self.assertIn("250.00", text)
        self.assertNotIn("100.00", text)

    def test_payment_list_beyond_limit_is_partial(self):
        self.db.payment(1, "ACME", "2026-09-01", "100")
        self.db.payment(2, "ACME", "2026-10-01", "250")
        answer = self.run_op("pagos", "ACME", limit=1)
        self.assertEqual(answer.status, "partial")
        self.assertIn("No es una lista completa", answer.text)
        self.assertIn("Pagos que le hicimos", answer.text)

    def test_payment_dates_show_no_time(self):
        self.db.payment(1, "ACME", "2026-10-01 00:00:00", "100")
        self.assertNotIn("00:00:00", self.run_op("ultimo_pago", "ACME").text)
        self.assertNotIn("00:00:00", self.run_op("pagos", "ACME").text)

    def test_payments_of_another_supplier_are_excluded(self):
        self.db.supplier("OTRO", "Otro SA", "1")
        self.db.payment(1, "OTRO", "2026-10-01", "777")
        self.assertEqual(self.run_op("pagos", "ACME").status, "empty")

    def test_invalid_period_is_rejected(self):
        with self.assertRaisesRegex(BusinessError, "período válido"):
            self.run_op("pagos", "ACME", period="rango", start="ayer", end="hoy")

    def test_last_payment_rejects_a_period(self):
        with self.assertRaisesRegex(BusinessError, "filtro de fechas"):
            self.run_op("ultimo_pago", "ACME", period="este_mes")


class SupplierNoteTests(SupplierCase):
    def test_last_note_sums_lines_not_total(self):
        self.db.purchase(1, 7, "ACME", "2026-10-01", "100", total="150")
        self.db.purchase(2, 7, "ACME", "2026-10-01", "100", total="200")
        text = self.run_op("ultima_nota", "ACME").text
        self.assertTrue(text.startswith("Proveedor Acme SA (código ACME)"))
        self.assertIn("folio 7", text)
        self.assertIn("Importe de la nota: 200.00", text)
        self.assertIn("Maíz", text)

    def test_last_note_ignores_cancelled(self):
        self.db.purchase(1, 7, "ACME", "2026-10-01", "100")
        self.db.purchase(2, 9, "ACME", "2026-10-05", "500", state="CANCELADA")
        self.assertNotIn("folio 9", self.run_op("ultima_nota", "ACME").text)

    def test_last_note_without_purchases_is_empty(self):
        self.assertEqual(self.run_op("ultima_nota", "ACME").status, "empty")

    def test_same_date_notes_warn_about_order(self):
        self.db.purchase(1, 7, "ACME", "2026-10-01", "100")
        self.db.purchase(2, 8, "ACME", "2026-10-01", "100")
        text = self.run_op("ultima_nota", "ACME").text
        self.assertIn("folio 8", text)
        self.assertIn("Comparten fecha", text)

    def test_movements_merge_purchases_and_payments_newest_first(self):
        self.db.purchase(1, 7, "ACME", "2026-10-05", "100")
        self.db.purchase(2, 7, "ACME", "2026-10-05", "50")
        self.db.payment(3, "ACME", "2026-10-03", "40")
        self.db.purchase(4, 6, "ACME", "2026-10-01", "10")
        lines = self.run_op("movimientos", "ACME", limit=3).text.splitlines()
        self.assertIn("compra · folio 7 · +150.00", lines[1])
        self.assertIn("pago · folio 3 · −40.00", lines[2])
        self.assertIn("compra · folio 6", lines[3])

    def test_last_note_with_two_dates_shows_the_whole_note(self):
        self.db.purchase(1, 7, "ACME", "2026-10-01", "100")
        self.db.purchase(2, 7, "ACME", "2026-10-02", "50")
        self.assertIn("Importe de la nota: 150.00", self.run_op("ultima_nota", "ACME").text)

    def test_movements_never_look_like_a_party_list(self):
        self.db.purchase(1, 7, "ACME", "2026-10-05", "100")
        self.db.payment(2, "ACME", "2026-10-03", "40")
        self.assertEqual(listed_codes(self.run_op("movimientos", "ACME").text), [])

    def test_movements_beyond_limit_are_partial(self):
        self.db.purchase(1, 7, "ACME", "2026-10-05", "100")
        self.db.payment(2, "ACME", "2026-10-03", "40")
        answer = self.run_op("movimientos", "ACME", limit=1)
        self.assertEqual(answer.status, "partial")
        self.assertIn("No es un estado de cuenta completo.", answer.text)

    def test_movements_respect_period_and_exclude_cancelled(self):
        self.db.purchase(1, 7, "ACME", "2026-09-05", "100")
        self.db.payment(2, "ACME", "2026-10-03", "40", state="CANCELADA")
        answer = self.run_op("movimientos", "ACME", period="este_mes")
        self.assertEqual(answer.status, "empty")

    def test_movements_report_current_balance(self):
        self.db.purchase(1, 7, "ACME", "2026-10-05", "100")
        self.assertIn("Saldo actual: 1,500.00", self.run_op("movimientos", "ACME").text)

    def test_pending_notes_square_with_balance(self):
        self.db.supplier("CUA", "Cuadra SA", "150", opening="0")
        self.db.purchase(1, 1, "CUA", "2026-09-01", "100")
        self.db.purchase(2, 2, "CUA", "2026-09-10", "100")
        self.db.payment(3, "CUA", "2026-09-20", "50")
        text = self.run_op("notas_pendientes", "CUA").text
        self.assertIn("folio 1", text)
        self.assertIn("pendiente 50.00 (parcial)", text)
        self.assertIn("Cuadra con el saldo actual", text)

    def test_paid_notes_are_not_listed(self):
        self.db.supplier("PAG", "Pagado SA", "100", opening="0")
        self.db.purchase(1, 1, "PAG", "2026-09-01", "100")
        self.db.purchase(2, 2, "PAG", "2026-09-10", "100")
        self.db.payment(3, "PAG", "2026-09-20", "100")
        text = self.run_op("notas_pendientes", "PAG").text
        self.assertNotIn("folio 1 ", text)
        self.assertIn("folio 2", text)

    def test_pending_notes_mismatch_is_reported(self):
        self.db.supplier("DES", "Descuadre SA", "999", opening="0")
        self.db.purchase(1, 1, "DES", "2026-09-01", "100")
        answer = self.run_op("notas_pendientes", "DES")
        self.assertEqual(answer.status, "partial")
        self.assertIn("no cuadran", answer.text)

    def test_favor_balance_has_no_pending_notes(self):
        self.db.supplier("FAV", "Favor SA", "-20", opening="0")
        self.db.payment(1, "FAV", "2026-09-20", "20")
        self.assertIn("a nuestro favor", self.run_op("notas_pendientes", "FAV").text)


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
