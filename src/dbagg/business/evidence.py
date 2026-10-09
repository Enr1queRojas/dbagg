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


def render_exploratory(result):
    """No narrative or calculated totals supplied by the model are trusted here."""
    rows = result.get("rows", [])
    columns = result["columns"]
    if any(len(row) != len(columns) for row in rows) or len(set(columns)) != len(columns):
        raise BusinessError("La fuente devolvió un resultado inconsistente.")
    if not rows:
        return "La consulta exploratoria no devolvió filas. Esto no confirma un saldo cero."
    lines = ["Resultado de consulta exploratoria; su definición de negocio requiere revisión:"]
    for row in rows[:10]:
        line = " · ".join(
            f"{label(c)}: {label(v, 300) if v is not None and str(v).strip() else 'sin dato'}"
            for c, v in zip(columns, row)
        )
        if len("\n".join(lines + [line])) > 2900:
            break
        lines.append(line)
    if len(lines) == 1:
        return "El resultado exploratorio no cabe en el mensaje. Acota las columnas o el período."
    lines.append("Muestra limitada; no sumar estas filas para obtener un total general.")
    if result.get("values_may_be_truncated") or result.get("response_size_limit_reached"):
        lines.append("Hay valores o filas recortados.")
    return "\n".join(lines)
