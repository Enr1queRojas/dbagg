"""The PDF is read back, so these tests see what a person would."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

from dbagg.business.parties import CUSTOMER, SUPPLIER
from dbagg.business.statements import Statement, StatementRow
from dbagg.reporting.statement_pdf import statement_filename, statement_pdf


def row(kind="COMPRA", saldo="100", total="100", abono="0", devolucion="0", **more):
    values = {
        "fecha": date(2026, 9, 29),
        "folio": "13440",
        "cantidad": Decimal("7038"),
        "codigo": "MAIZ",
        "concepto": "N/A",
        "precio": Decimal("8.30"),
        **more,
    }
    return StatementRow(
        kind=kind,
        total=Decimal(total),
        abono=Decimal(abono),
        devolucion=Decimal(devolucion),
        saldo=Decimal(saldo),
        **values,
    )


def customer_statement():
    return Statement(
        entity=CUSTOMER,
        code="A.ESPARZA3",
        name="ALEJANDRO ESPARZA",
        inactive=False,
        start=date(2026, 8, 30),
        end=date(2026, 11, 4),
        opening=Decimal("49485.52"),
        rows=(
            row("CRÉDITO", saldo="107900.92", total="58415.40"),
            row(
                "COBRANZA",
                saldo="59850.60",
                total="0",
                abono="48050.32",
                codigo="SANTANDER",
                concepto="ABONO",
            ),
        ),
        closing=Decimal("59850.60"),
        current=Decimal("59850.60"),
        warnings=(),
    )


def supplier_statement(**changes):
    base = replace(
        customer_statement(),
        entity=SUPPLIER,
        code="CUERVO",
        name="JOHAN WIEBE",
        rows=(row(saldo="150", total="150"),),
    )
    return replace(base, **changes)


def read_pdf(content):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)


class StatementPdfTests(unittest.TestCase):
    def test_customer_pdf_shows_header_rows_and_balances(self):
        content = statement_pdf(customer_statement())
        self.assertTrue(content.startswith(b"%PDF"))
        text = read_pdf(content)
        for expected in (
            "ESTADO DE CUENTA",
            "A.ESPARZA3",
            "ALEJANDRO ESPARZA",
            "30/08/2026",
            "04/11/2026",
            "49,485.52",
            "107,900.92",
            "59,850.60",
            "58,415.40",
            "48,050.32",
            "29/09/2026",
            "CRÉDITO",
            "COBRANZA",
            "SANTANDER",
            "Devolución",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_supplier_pdf_has_no_returns_column(self):
        text = read_pdf(statement_pdf(supplier_statement()))
        self.assertNotIn("DEVOLUCIÓN", text.upper())
        self.assertIn("Abono", text)

    def test_warnings_are_visible(self):
        statement = supplier_statement(warnings=("Los movimientos no cuadran con el saldo.",))
        self.assertIn("no cuadran", read_pdf(statement_pdf(statement)))

    def test_old_period_shows_both_balances(self):
        statement = supplier_statement(
            end=date(2026, 9, 30), closing=Decimal("10"), current=Decimal("20")
        )
        text = read_pdf(statement_pdf(statement))
        self.assertIn("SALDO AL 30/09/2026", text)
        self.assertIn("SALDO ACTUAL", text)
        self.assertIn("20.00", text)

    def test_unknown_current_balance_is_not_shown_as_zero(self):
        text = read_pdf(statement_pdf(supplier_statement(current=None)))
        self.assertIn("Desconocido", text)

    def test_empty_period_says_so(self):
        text = read_pdf(statement_pdf(supplier_statement(rows=())))
        self.assertIn("Sin movimientos", text)

    def test_inactive_supplier_is_marked(self):
        text = read_pdf(statement_pdf(supplier_statement(inactive=True)))
        self.assertIn("inactivo", text)

    def test_accents_and_many_rows_survive(self):
        rows = tuple(row(saldo=str(i), folio=str(i)) for i in range(250))
        content = statement_pdf(supplier_statement(name="PEÑA ÁVILA", rows=rows))
        reader = PdfReader(BytesIO(content))
        self.assertGreater(len(reader.pages), 1)
        last = reader.pages[-1].extract_text()
        self.assertIn("PEÑA ÁVILA", last)
        self.assertIn("Saldo", last)
        self.assertIn("249.00", last)

    def test_long_concept_does_not_lose_the_balance_column(self):
        rows = (row(concepto="CONCEPTO MUY LARGO " * 30, saldo="4321.09"),)
        text = read_pdf(statement_pdf(supplier_statement(rows=rows)))
        self.assertIn("4,321.09", text)

    def test_markup_in_a_concept_is_literal(self):
        rows = (row(concepto="MAIZ <b>A&B</b>"),)
        text = read_pdf(statement_pdf(supplier_statement(rows=rows)))
        self.assertIn("A&B", text)

    def test_metadata_has_no_party_data(self):
        reader = PdfReader(BytesIO(statement_pdf(supplier_statement(name="PEÑA ÁVILA"))))
        self.assertNotIn("PEÑA", str(reader.metadata))
        self.assertNotIn("CUERVO", str(reader.metadata))

    def test_filename_is_safe(self):
        self.assertEqual(
            statement_filename(supplier_statement(code="PEDRO 60 HECT")),
            "estado-cuenta-PEDRO-60-HECT-2026-08-30-2026-11-04.pdf",
        )
        self.assertEqual(
            statement_filename(supplier_statement(code="../ÑU/x")),
            "estado-cuenta-NU-x-2026-08-30-2026-11-04.pdf",
        )
