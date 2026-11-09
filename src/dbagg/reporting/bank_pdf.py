"""Renders a bank-account report as an in-memory PDF; amounts come from the data, never the model."""

import re
import unicodedata
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dbagg.business.evidence import amount
from dbagg.reporting.statement_pdf import CELL, EMPTY, GRID, HEADING, WARNING

MARGIN = 0.5 * inch
HEADER_SPACE = 0.75 * inch
PAGE_WIDTH, PAGE_HEIGHT = letter
COLUMNS = ("Fecha", "Concepto", "Folio", "Depósito", "Retiro")
WIDTHS = (62, 194, 64, 90, 90)


def _day(value):
    return f"{value:%d/%m/%Y}"


def _style(table, numeric_from, body_from=0):
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("FONTSIZE", (0, 0), (-1, -1), 9 if body_from else 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, 0), (-1, 0), HEADING),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    return table


def _summary(report):
    subject = report.name + (" (cuenta inactiva)" if report.inactive else "")
    current = "Desconocido" if report.current is None else amount(report.current)
    rows = [
        ["REPORTE DE BANCO", Paragraph(f"{escape(report.code)} · {escape(subject)}", CELL)],
        ["PERÍODO", f"DEL {_day(report.start)} AL {_day(report.end)}"],
        ["DEPÓSITOS", amount(report.deposits)],
        ["RETIROS", amount(report.withdrawals)],
        ["NETO DEL PERÍODO", amount(report.deposits - report.withdrawals)],
        ["SALDO ACTUAL", current],
    ]
    table = Table(rows, colWidths=(150, 350), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("BACKGROUND", (0, 0), (0, -1), HEADING),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _concepts(report):
    data = [["Concepto", "Movimientos", "Depósitos", "Retiros"]]
    for concept, deposit, count, value in report.concepts:
        data.append(
            [
                concept,
                str(count),
                amount(value) if deposit else "",
                "" if deposit else amount(value),
            ]
        )
    return _style(Table(data, colWidths=(220, 80, 100, 100), hAlign="LEFT"), 1, body_from=1)


def _movements(report):
    data = [list(COLUMNS)]
    for row in report.rows:
        data.append(
            [
                _day(row.fecha),
                row.concepto,
                row.folio,
                amount(row.deposito) if row.deposito else "",
                amount(row.retiro) if row.retiro else "",
            ]
        )
    data.append(["", "TOTAL", "", amount(report.deposits), amount(report.withdrawals)])
    table = _style(LongTable(data, colWidths=WIDTHS, repeatRows=1, hAlign="LEFT"), 3)
    table.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    return table


def _page_header(report):
    def draw(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        top = PAGE_HEIGHT - 0.4 * inch
        canvas.drawString(MARGIN, top, f"Reporte de banco · {report.code} · {report.name}")
        canvas.drawRightString(PAGE_WIDTH - MARGIN, top, f"Página {document.page}")
        canvas.restoreState()

    return draw


def bank_report_pdf(report):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=HEADER_SPACE,
        bottomMargin=MARGIN,
        title="Reporte de banco",
        author="dbagg",
    )
    story = [_summary(report), Spacer(1, 8)]
    story += [Paragraph(escape(warning), WARNING) for warning in report.warnings]
    if report.rows:
        story += [_concepts(report), Spacer(1, 10), _movements(report)]
    else:
        story.append(Paragraph("Sin movimientos en este período.", EMPTY))
    story.append(Paragraph("Importes en pesos.", EMPTY))
    draw = _page_header(report)
    document.build(story, onFirstPage=draw, onLaterPages=draw)
    return buffer.getvalue()


def bank_report_filename(report):
    plain = unicodedata.normalize("NFKD", report.code).encode("ascii", "ignore").decode()
    code = re.sub(r"[^A-Za-z0-9.]+", "-", plain).strip("-.") or "sin-codigo"
    return f"reporte-banco-{code}-{report.start:%Y-%m-%d}-{report.end:%Y-%m-%d}.pdf"
