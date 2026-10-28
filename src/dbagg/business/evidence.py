"""Results are rendered from data; ungrounded model prose is never a final answer."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import uuid4


class BusinessError(ValueError):
    """Safe, operator-authored explanation of a business contract failure."""


@dataclass(frozen=True)
class BusinessAnswer:
    text: str
    status: str
    operation: str
    sources: tuple[str, ...]
    evidence_id: str

    @classmethod
    def make(cls, text, status, operation, sources):
        return cls(text, status, operation, tuple(sources), uuid4().hex)


def records(result):
    if result.get("values_may_be_truncated") or result.get("response_size_limit_reached"):
        raise BusinessError("El resultado está recortado; no puedo confirmar los datos completos.")
    columns = result["columns"]
    if len(set(columns)) != len(columns) or any(len(row) != len(columns) for row in result["rows"]):
        raise BusinessError("La fuente devolvió un resultado inconsistente.")
    return [dict(zip(columns, row)) for row in result["rows"]]


def amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return f"{number:,.2f}"
    except (InvalidOperation, ValueError):
        raise BusinessError("El importe no está disponible o es inválido.") from None


def label(value, max_length=100):
    if value is None or not str(value).strip() or len(str(value)) > max_length:
        raise BusinessError("El identificador o nombre del cliente requiere revisión.")
    # Preserve identifiers exactly apart from display whitespace; never rewrite amounts.
    return " ".join(str(value).split())


def display_label(value, missing_text):
    """Display an already bounded result cell without claiming a verified identity."""
    text = " ".join(str(value).split()) if value is not None else ""
    return (text, False) if text else (missing_text, True)


def _cell(value):
    """Decimal values get thousands separators and two decimals; codes and years stay as-is."""
    if value is None or not str(value).strip():
        return "sin dato"
    text = label(value, 300)
    if "." in text:
        try:
            number = Decimal(text)
            if number.is_finite():
                return f"{number:,.2f}"
        except InvalidOperation:
            pass
    return text


def _is_number(text):
    try:
        return Decimal(text.replace(",", "")).is_finite()
    except InvalidOperation:
        return False


def table_lines(header, cells):
    """Header, dashes and rows of display strings; numeric columns are right-aligned."""
    numeric = [all(_is_number(r[i]) for r in cells) for i in range(len(header))]
    widths = [max(len(header[i]), *(len(r[i]) for r in cells)) for i in range(len(header))]

    def line(values):
        return "  ".join(
            v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i]) for i, v in enumerate(values)
        ).rstrip()

    return [line(header), "  ".join("-" * w for w in widths)] + [line(r) for r in cells]


def render_exploratory(result):
    """No narrative or calculated totals supplied by the model are trusted here.

    Rows are shown as a monospace table (WhatsApp ``` block) with numbers right-aligned.
    """
    rows = result.get("rows", [])
    columns = result["columns"]
    if any(len(row) != len(columns) for row in rows) or len(set(columns)) != len(columns):
        raise BusinessError("La fuente devolvió un resultado inconsistente.")
    if not rows:
        return "La consulta exploratoria no devolvió filas. Esto no confirma un saldo cero."
    intro = "Resultado de consulta exploratoria; su definición de negocio requiere revisión:"
    full = table_lines([label(c) for c in columns], [[_cell(v) for v in row] for row in rows[:10]])
    table = full[:2]
    for row_line in full[2:]:
        if len(intro) + len("\n".join(table)) + len(row_line) > 2850:
            break
        table.append(row_line)
    if len(table) == 2:
        return "El resultado exploratorio no cabe en el mensaje. Acota las columnas o el período."
    lines = [intro, "```", *table, "```"]
    if result.get("has_more") or len(table) - 2 < len(rows):
        lines.append("Muestra limitada; no sumar estas filas para obtener un total general.")
    if result.get("values_may_be_truncated") or result.get("response_size_limit_reached"):
        lines.append("Hay valores o filas recortados.")
    return "\n".join(lines)
