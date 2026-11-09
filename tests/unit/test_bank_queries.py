"""Synthetic bank-account scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import sqlite3
import unittest
from datetime import date

import sqlglot

from dbagg.business.banks import (
    BANK_DETAIL,
    BANK_SOURCES,
    BANKS,
    DEPOSIT_COLUMNS,
    MOVEMENT_SOURCES,
    WITHDRAWAL_COLUMNS,
    BankQueries,
    validate_bank_context,
)
from dbagg.business.evidence import BusinessError
from dbagg.context.loader import load_business_context
from dbagg.database.catalog import load_catalog
from dbagg.limits import MAX_ROWS

TODAY = date(2026, 11, 9)


def _definitions():
    definitions = {
        BANKS: {
            "CODIGO": "nvarchar",
            "CUENTA BANCARIA": "nvarchar",
            "SALDO ACTUAL": "decimal",
            "DEPOSITOS": "decimal",
            "RETIROS": "decimal",
            "SALDO INICIAL": "decimal",
            "ESTADO": "nvarchar",
        },
        BANK_DETAIL: {
            "CODIGO": "nvarchar",
            **{c: "decimal" for c in DEPOSIT_COLUMNS},
            **{c: "decimal" for c in WITHDRAWAL_COLUMNS},
        },
    }
    for source in MOVEMENT_SOURCES:
        columns = definitions.setdefault(
            source.table,
            {
                "ID": "bigint",
                "FOLIO": "bigint",
                "FECHA": "date",
                "IMPORTE": "decimal",
                "ESTADO": "nvarchar",
            },
        )
        columns[source.column] = "nvarchar"
    return definitions


DEFINITIONS = _definitions()


class BankDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS proadel")
        for table, columns in DEFINITIONS.items():
            names = ", ".join(
                f'"{c}" NUMERIC' if kind in {"decimal", "bigint"} else f'"{c}"'
                for c, kind in columns.items()
            )
            self.conn.execute(f"CREATE TABLE {table} ({names})")
        self.next_id = 1

    def schema(self, sources):
        if not 1 <= len(set(sources)) <= 5:
            raise ValueError("Selecciona entre una y cinco tablas autorizadas.")  # as the client
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
        if not 1 <= row_limit <= MAX_ROWS:
            raise ValueError("Límite de filas inválido.")  # as the real client
        rendered = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
        params = tuple(p.isoformat() if isinstance(p, date) else p for p in params)
        cursor = self.conn.execute(rendered, params)
        rows = cursor.fetchmany(row_limit + 1)
        return {
            "columns": [c[0] for c in cursor.description],
            "rows": [[None if v is None else str(v) for v in row] for row in rows[:row_limit]],
            "has_more": len(rows) > row_limit,
        }

    def account(self, code, name, opening="0", state="ACTIVO", **concepts):
        """Catalog row whose totals are derived from the concept columns, as in production."""
        detail = {c: 0 for c in DEPOSIT_COLUMNS + WITHDRAWAL_COLUMNS}
        detail.update(concepts)
        deposits = sum(float(detail[c]) for c in DEPOSIT_COLUMNS)
        withdrawals = sum(float(detail[c]) for c in WITHDRAWAL_COLUMNS)
        balance = float(opening) + deposits - withdrawals
        self.conn.execute(
            f"INSERT INTO {BANKS} VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, name, balance, deposits, withdrawals, opening, state),
        )
        columns = list(DEFINITIONS[BANK_DETAIL])
        self.conn.execute(
            f"INSERT INTO {BANK_DETAIL} VALUES ({', '.join('?' * len(columns))})",
            [code] + [detail[c] for c in columns[1:]],
        )

    def movement(self, table, column, bank, fecha, value, folio=None, state="ACTIVA", other=None):
        """One source row; ``other`` fills the opposite bank column of a transfer."""
        columns = list(DEFINITIONS[table])
        row = {c: None for c in columns}
        row.update(ID=self.next_id, FOLIO=folio or self.next_id, FECHA=fecha, IMPORTE=value)
        row.update(ESTADO=state)
        row[column] = bank
        if other:
            row.update(other)
        self.next_id += 1
        self.conn.execute(
            f"INSERT INTO {table} VALUES ({', '.join('?' * len(columns))})",
            [row[c] for c in columns],
        )


class BankCase(unittest.TestCase):
    def setUp(self):
        self.db = BankDatabase()
        self.addCleanup(self.db.conn.close)
        self.queries = BankQueries(self.db, set(BANK_SOURCES), today=TODAY)
        self.db.account(
            "SANTANDER",
            "SANTANDER CUENTA",
            opening="100",
            DEPOSITOS_COBRANZA=1000,
            RETIROS_GASTOS=300,
        )

    def run_op(self, operation, banco=None, **kwargs):
        return self.queries.execute(operation, banco=banco, **kwargs)


class BankBalanceTests(BankCase):
    def test_balance_heads_with_account_and_shows_current_balance(self):
        answer = self.run_op("saldo_banco", "SANTANDER")
        lines = answer.text.splitlines()
        self.assertEqual(lines[0], "Cuenta SANTANDER (SANTANDER CUENTA)")
        self.assertIn("Saldo actual: 800.00.", answer.text)
        self.assertEqual(answer.status, "answered")

    def test_balance_lists_only_nonzero_concepts(self):
        text = self.run_op("saldo_banco", "SANTANDER").text
        self.assertIn("• Cobranza: 1,000.00", text)
        self.assertIn("• Gastos: 300.00", text)
        self.assertNotIn("Rentas", text)

    def test_negative_balance_is_shown_as_is(self):
        self.db.account(
            "CRET-SANTANDER", "CREDITO SANTANDER", opening="-500", RETIROS_PRESTAMOS=100
        )
        text = self.run_op("saldo_banco", "CRET-SANTANDER").text
        self.assertIn("Saldo actual: -600.00.", text)
        self.assertNotIn("debemos", text.lower())

    def test_amounts_are_in_pesos(self):
        self.assertIn("Importes en pesos.", self.run_op("saldo_banco", "SANTANDER").text)

    def test_breakdown_that_does_not_square_is_warned(self):
        self.db.conn.execute(f"UPDATE {BANKS} SET DEPOSITOS = 999 WHERE CODIGO = 'SANTANDER'")
        answer = self.run_op("saldo_banco", "SANTANDER")
        self.assertIn("no cuadra", answer.text)
        self.assertEqual(answer.status, "partial")

    def test_inactive_account_is_marked(self):
        self.db.account("ALTERNOS", "SALDOS ALTERNOS", state="INACTIVO")
        self.assertIn("Cuenta inactiva", self.run_op("saldo_banco", "ALTERNOS").text)

    def test_unknown_balance_is_unavailable(self):
        self.db.conn.execute(f"UPDATE {BANKS} SET [SALDO ACTUAL] = NULL")
        answer = self.run_op("saldo_banco", "SANTANDER")
        self.assertEqual(answer.status, "unavailable")
        self.assertNotIn("0.00", answer.text.splitlines()[1])


class BankResolutionTests(BankCase):
    def setUp(self):
        super().setUp()
        self.db.account("CRET-SANTANDER", "CREDITO SANTANDER")
        self.db.account("NSANTANDER", "NATALIA SANTANDER")

    def test_exact_code_wins_over_partial_matches(self):
        text = self.run_op("saldo_banco", "santander").text
        self.assertTrue(text.startswith("Cuenta SANTANDER (SANTANDER CUENTA)"))

    def test_exact_account_name_resolves(self):
        text = self.run_op("saldo_banco", "Credito Santander").text
        self.assertTrue(text.startswith("Cuenta CRET-SANTANDER (CREDITO SANTANDER)"))

    def test_accents_in_the_question_are_ignored(self):
        self.db.account("BAJIO", "BAJIO CUENTA")
        self.assertTrue(self.run_op("saldo_banco", "Bajío").text.startswith("Cuenta BAJIO"))

    def test_several_partial_matches_ask_to_choose(self):
        answer = self.run_op("saldo_banco", "SANTAND")
        self.assertEqual(answer.status, "needs_clarification")
        self.assertIn("• CRET-SANTANDER (CREDITO SANTANDER)", answer.text)

    def test_cash_account_of_a_person_resolves_by_name(self):
        self.db.account("DON LEO", "SALDOS DON LEO", opening="50")
        self.assertIn("Saldo actual: 50.00.", self.run_op("saldo_banco", "don leo").text)

    def test_unknown_account_asks_to_clarify(self):
        answer = self.run_op("saldo_banco", "HSBC")
        self.assertEqual(answer.status, "needs_clarification")
        self.assertIn("No encontré una cuenta", answer.text)

    def test_missing_account_is_rejected(self):
        with self.assertRaises(BusinessError):
            self.run_op("saldo_banco", None)

    def test_like_wildcards_are_literal(self):
        self.assertEqual(self.run_op("saldo_banco", "%").status, "needs_clarification")


class AllBalancesTests(BankCase):
    def test_lists_each_active_account_without_a_total(self):
        self.db.account("ENRIQUE", "EFECTIVOS ENRIQUE ROJAS", opening="-20")
        answer = self.run_op("saldos_bancos")
        self.assertIn("• ENRIQUE (EFECTIVOS ENRIQUE ROJAS): -20.00", answer.text)
        self.assertIn("• SANTANDER (SANTANDER CUENTA): 800.00", answer.text)
        self.assertIn("no se suman", answer.text)
        self.assertNotIn("Total", answer.text)

    def test_inactive_accounts_in_zero_are_counted_not_listed(self):
        self.db.account("ALTERNOS", "SALDOS ALTERNOS", state="INACTIVO")
        text = self.run_op("saldos_bancos").text
        self.assertNotIn("ALTERNOS", text)
        self.assertIn("1 cuenta inactiva en cero", text)

    def test_inactive_account_with_balance_is_listed_and_marked(self):
        self.db.account("VIEJA", "CUENTA VIEJA", opening="10", state="INACTIVO")
        self.assertIn("• VIEJA (CUENTA VIEJA): 10.00 · inactiva", self.run_op("saldos_bancos").text)

    def test_rejects_an_account_argument(self):
        with self.assertRaises(BusinessError):
            self.run_op("saldos_bancos", "SANTANDER")


class MovementTests(BankCase):
    def setUp(self):
        super().setUp()
        self.cobranza = self.source("proadel.cobranza_data", "BANCO")
        self.gastos = self.source("proadel.gastos_data", "RETIRADO DE")
        self.contado = self.source("proadel.contado_data", "BANCO")

    def source(self, table, column):
        return next(s for s in MOVEMENT_SOURCES if s.table == table and s.column == column)

    def add(self, source, fecha, value, **kwargs):
        self.db.movement(source.table, source.column, "SANTANDER", fecha, value, **kwargs)

    def test_lists_latest_deposits_and_withdrawals_with_sign(self):
        self.add(self.cobranza, "2026-11-02", 1000, folio=10)
        self.add(self.gastos, "2026-11-03", 300, folio=20)
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes").text
        self.assertIn("• 2026-11-03 · Gastos · folio 20 · −300.00", text)
        self.assertIn("• 2026-11-02 · Cobranza · folio 10 · +1,000.00", text)
        self.assertLess(text.index("2026-11-03"), text.index("2026-11-02"))

    def test_period_totals_cover_all_rows_not_only_the_listed_ones(self):
        for day in range(1, 8):
            self.add(self.cobranza, f"2026-11-0{day}", 100)
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes", limit=2).text
        self.assertIn("Depósitos: 700.00 · Retiros: 0.00.", text)
        self.assertIn("• Cobranza: +700.00 (7)", text)
        self.assertIn("Hay más movimientos", text)

    def test_cancelled_and_out_of_period_rows_are_excluded(self):
        self.add(self.cobranza, "2026-11-02", 1000)
        self.add(self.cobranza, "2026-11-02", 5000, state="CANCELADA")
        self.add(self.cobranza, "2026-10-31", 7000)
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes").text
        self.assertIn("Depósitos: 1,000.00", text)
        self.assertNotIn("7,000.00", text)
        self.assertNotIn("5,000.00", text)

    def test_transfer_counts_by_side(self):
        sent = self.source("proadel.transferencias_bancos_data", "BANCO ORIGEN")
        self.db.movement(
            sent.table,
            sent.column,
            "SANTANDER",
            "2026-11-04",
            250,
            other={"BANCO DESTINO": "BAJIO"},
        )
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes").text
        self.assertIn("Transferencias enviadas · folio", text)
        self.assertIn("Retiros: 250.00", text)

    def test_cash_sale_items_group_by_folio(self):
        self.add(self.contado, "2026-11-05", 100, folio=7)
        self.add(self.contado, "2026-11-05", 50, folio=7)
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes").text
        self.assertIn("Ventas de contado · folio 7 · +150.00", text)
        self.assertIn("• Ventas de contado: +150.00 (1)", text)

    def test_other_accounts_rows_are_ignored(self):
        self.db.movement(self.cobranza.table, "BANCO", "BAJIO", "2026-11-02", 999)
        answer = self.run_op("movimientos_banco", "SANTANDER", period="este_mes")
        self.assertEqual(answer.status, "empty")
        self.assertIn("No encontré movimientos", answer.text)

    def test_undated_rows_are_warned(self):
        self.add(self.cobranza, None, 100)
        answer = self.run_op("movimientos_banco", "SANTANDER", period="este_mes")
        self.assertIn("sin fecha válida", answer.text)
        self.assertEqual(answer.status, "partial")

    def test_charges_without_a_source_are_warned(self):
        self.db.account("ELEAZAR", "SALDOS ELEAZAR", RETIROS_MANIOBRAS=40)
        text = self.run_op("movimientos_banco", "ELEAZAR").text
        self.assertIn("Maniobras", text)
        self.assertIn("no aparecen", text)

    def test_shows_period_and_current_balance(self):
        self.add(self.cobranza, "2026-11-02", 1000)
        text = self.run_op("movimientos_banco", "SANTANDER", period="este_mes").text
        self.assertIn("Del 01/11/2026 al 09/11/2026.", text)
        self.assertIn("Saldo actual: 800.00.", text)

    def test_balance_rejects_a_period(self):
        with self.assertRaises(BusinessError):
            self.run_op("saldo_banco", "SANTANDER", period="este_mes")


class ContractTests(unittest.TestCase):
    def test_unsupported_operation_is_rejected(self):
        queries = BankQueries(BankDatabase(), set(BANK_SOURCES), today=TODAY)
        with self.assertRaises(BusinessError):
            queries.execute("borrar_banco", banco="SANTANDER")

    def test_unauthorised_sources_are_rejected(self):
        queries = BankQueries(BankDatabase(), {BANKS}, today=TODAY)
        with self.assertRaises(BusinessError):
            queries.execute("saldo_banco", banco="SANTANDER")

    def test_every_source_is_catalogued(self):
        catalog = load_catalog()
        for source in BANK_SOURCES:
            self.assertIn(source, catalog.sources)
        for source in (BANKS, BANK_DETAIL):
            self.assertEqual(catalog.sources[source].access, "business_tool")

    def test_catalogued_columns_cover_the_queries(self):
        catalog = load_catalog()
        for table, columns in DEFINITIONS.items():
            self.assertLessEqual({c.lower() for c in columns}, catalog.sources[table].columns)

    def test_default_context_matches_the_contract(self):
        validate_bank_context(load_business_context())

    def test_changed_context_is_rejected(self):
        context = json.loads(load_business_context())
        context["topics"]["bank_accounts"]["preferred_sources"] = ["proadel.OTRA"]
        with self.assertRaises(BusinessError):
            validate_bank_context(json.dumps(context))


if __name__ == "__main__":
    unittest.main()
