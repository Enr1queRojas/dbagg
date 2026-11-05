"""Renders a statement as an in-memory PDF; amounts come from the data, never from the model."""

import re
import unicodedata
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dbagg.business.evidence import amount
from dbagg.business.parties import CUSTOMER

MARGIN = 0.5 * inch
HEADER_SPACE = 0.75 * inch
PAGE_WIDTH, PAGE_HEIGHT = landscape(letter)
GRID = colors.HexColor("#c9c9c9")
HEADING = colors.HexColor("#e9edf2")
ALERT = colors.HexColor("#b00020")
CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=9.5)
WARNING = ParagraphStyle(
    "warning", fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=ALERT, spaceAfter=3
)
EMPTY = ParagraphStyle("empty", fontName="Helvetica", fontSize=10, leading=12, spaceBefore=8)
COLUMNS = ("Tipo", "Fecha", "Folio", "Cantidad", "Código", "Concepto", "Precio", "Total", "Abono")
COLUMNS_WITH_RETURNS = (*COLUMNS, "Devolución", "Saldo")
COLUMNS_WITHOUT_RETURNS = (*COLUMNS, "Saldo")
WIDTHS_WITH_RETURNS = (58, 50, 42, 54, 62, 136, 50, 66, 66, 66, 70)
WIDTHS_WITHOUT_RETURNS = (58, 50, 42, 54, 62, 202, 50, 66, 66, 70)


def _day(value):
    return f"{value:%d/%m/%Y}"


def _summary(statement):
    subject = statement.name + (" (proveedor inactivo)" if statement.inactive else "")
    current = "Desconocido" if statement.current is None else amount(statement.current)
    rows = [
        ["ESTADO DE CUENTA", Paragraph(f"{escape(statement.code)} · {escape(subject)}", CELL)],
        ["PERÍODO", f"DEL {_day(statement.start)} AL {_day(statement.end)}"],
        ["SALDO ANTERIOR", amount(statement.opening)],
        [f"SALDO AL {_day(statement.end)}", amount(statement.closing)],
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


def _movements(statement):
    with_returns = statement.entity == CUSTOMER
    header = COLUMNS_WITH_RETURNS if with_returns else COLUMNS_WITHOUT_RETURNS
    data = [list(header)]
    for row in statement.rows:
        cells = [
            row.kind,
            _day(row.fecha),
            row.folio,
            amount(row.cantidad),
            Paragraph(escape(row.codigo), CELL),
            Paragraph(escape(row.concepto), CELL),
            amount(row.precio),
            amount(row.total),
            amount(row.abono),
        ]
        if with_returns:
            cells.append(amount(row.devolucion))
        cells.append(amount(row.saldo))
        data.append(cells)
    table = LongTable(
        data,
        colWidths=WIDTHS_WITH_RETURNS if with_returns else WIDTHS_WITHOUT_RETURNS,
        repeatRows=1,
    )
    numeric_from = 6
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("BACKGROUND", (0, 0), (-1, 0), HEADING),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (-1, 1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    return table


def _page_header(statement):
    def draw(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        top = PAGE_HEIGHT - 0.4 * inch
        canvas.drawString(MARGIN, top, f"Estado de cuenta · {statement.code} · {statement.name}")
        canvas.drawRightString(PAGE_WIDTH - MARGIN, top, f"Página {document.page}")
        canvas.restoreState()

    return draw


def statement_pdf(statement):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=HEADER_SPACE,
        bottomMargin=MARGIN,
        title="Estado de cuenta",
        author="dbagg",
    )
    story = [_summary(statement), Spacer(1, 8)]
    story += [Paragraph(escape(warning), WARNING) for warning in statement.warnings]
    if statement.rows:
        story.append(_movements(statement))
    else:
        story.append(Paragraph("Sin movimientos en este período.", EMPTY))
    draw = _page_header(statement)
    document.build(story, onFirstPage=draw, onLaterPages=draw)
    return buffer.getvalue()


def statement_filename(statement):
    plain = unicodedata.normalize("NFKD", statement.code).encode("ascii", "ignore").decode()
    code = re.sub(r"[^A-Za-z0-9.]+", "-", plain).strip("-.") or "sin-codigo"
    return f"estado-cuenta-{code}-{statement.start:%Y-%m-%d}-{statement.end:%Y-%m-%d}.pdf"
