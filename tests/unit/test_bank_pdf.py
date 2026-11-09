"""The PDF is read back, so these tests see what a person would."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

from dbagg.business.banks import BankMovement, BankReport
from dbagg.reporting.bank_pdf import bank_report_filename, bank_report_pdf


def report(**changes):
    values = dict(
        code="DON LEO",
        name="SALDOS DON LEO",
        inactive=False,
        start=date(2026, 11, 1),
        end=date(2026, 11, 9),
        deposits=Decimal("74000"),
        withdrawals=Decimal("31002"),
        concepts=(
            ("Transferencias recibidas", True, 1, Decimal("74000")),
            ("Gastos", False, 1, Decimal("31002")),
        ),
        rows=(
            BankMovement(
                date(2026, 11, 7), "Transferencias recibidas", "5115", Decimal("74000"), Decimal(0)
            ),
            BankMovement(date(2026, 11, 7), "Gastos", "20176", Decimal(0), Decimal("31002")),
        ),
        current=Decimal("116696.98"),
        warnings=(),
    )
    values.update(changes)
    return BankReport(**values)


def text_of(content):
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(content)).pages)


class BankPdfTests(unittest.TestCase):
    def test_summary_concepts_and_movements_are_shown(self):
        text = text_of(bank_report_pdf(report()))
        for expected in (
            "REPORTE DE BANCO",
            "DON LEO · SALDOS DON LEO",
            "DEL 01/11/2026 AL 09/11/2026",
            "74,000.00",
            "31,002.00",
            "42,998.00",
            "116,696.98",
            "Transferencias recibidas",
            "20176",
            "TOTAL",
            "Importes en pesos.",
        ):
            self.assertIn(expected, text)

    def test_warnings_and_empty_period_are_shown(self):
        content = bank_report_pdf(report(rows=(), concepts=(), warnings=("Revisar fechas.",)))
        text = text_of(content)
        self.assertIn("Revisar fechas.", text)
        self.assertIn("Sin movimientos en este período.", text)

    def test_unknown_current_balance(self):
        self.assertIn("Desconocido", text_of(bank_report_pdf(report(current=None))))

    def test_long_reports_repeat_the_header_on_each_page(self):
        rows = report().rows * 200
        reader = PdfReader(BytesIO(bank_report_pdf(report(rows=rows))))
        self.assertGreater(len(reader.pages), 1)
        self.assertIn("Fecha", reader.pages[-1].extract_text())

    def test_filename_is_ascii_and_dated(self):
        self.assertEqual(
            bank_report_filename(replace(report(), code="CAJA ÑÚ")),
            "reporte-banco-CAJA-NU-2026-11-01-2026-11-09.pdf",
        )
