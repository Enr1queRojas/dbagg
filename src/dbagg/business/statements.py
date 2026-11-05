"""Account statements of a customer or supplier, as data; rendering lives in reporting."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dbagg.business.calendar import business_today
from dbagg.business.customers import (
    CREDITS,
    CUSTOMERS,
    PAYMENTS,
    RETURNS,
    CatalogQueries,
    CustomerQueries,
    date_expression,
    decimal_value,
)
from dbagg.business.evidence import BusinessError, amount, records
from dbagg.business.parties import CUSTOMER, SUPPLIER
from dbagg.business.suppliers import PURCHASES, SUPPLIER_PAYMENTS, SUPPLIERS, SupplierQueries

STATEMENT_OPERATION = "estado_cuenta"
MAX_STATEMENT_ROWS = 2000
TOLERANCE = Decimal("0.01")
CHARGE, CREDIT, RETURN = "charge", "credit", "return"
ACTIVE = "[ESTADO] = 'ACTIVA'"
# Same-day charges come first: the balance then shows what a later credit settled.
SIDE_ORDER = {CHARGE: 0, CREDIT: 1, RETURN: 1}


@dataclass(frozen=True)
class _Source:
    kind: str
    table: str
    party_column: str
    side: str
    amount_column: str


SOURCES = {
    CUSTOMER: (
        _Source("CRÉDITO", CREDITS, "CLIENTE", CHARGE, "TOTAL"),
        _Source("COBRANZA", PAYMENTS, "CLIENTE", CREDIT, "IMPORTE"),
        _Source("DEVOLUCIÓN", RETURNS, "CLIENTE", RETURN, "IMPORTE"),
    ),
    SUPPLIER: (
        _Source("COMPRA", PURCHASES, "PROVEEDOR", CHARGE, "IMPORTE"),
        _Source("PAGO", SUPPLIER_PAYMENTS, "PROVEEDOR", CREDIT, "IMPORTE"),
    ),
}
CATALOGS = {CUSTOMER: CUSTOMERS, SUPPLIER: SUPPLIERS}


@dataclass(frozen=True)
class StatementRow:
    kind: str
    fecha: date
    folio: str
    cantidad: Decimal
    codigo: str
    concepto: str
    precio: Decimal
    total: Decimal
    abono: Decimal
    devolucion: Decimal
    saldo: Decimal


@dataclass(frozen=True)
class Statement:
    entity: str
    code: str
    name: str
    inactive: bool
    start: date
    end: date
    opening: Decimal
    rows: tuple
    closing: Decimal
    current: Decimal | None
    warnings: tuple


class StatementTooLarge(BusinessError):
    """More rows than a statement may hold; a partial document would mislead."""


def _too_large():
    return StatementTooLarge(
        f"Ese período tiene más de {MAX_STATEMENT_ROWS:,} movimientos. Indica fechas más cortas."
    )


def _folio_order(folio):
    try:
        return (0, Decimal(folio), "")
    except (ArithmeticError, TypeError, ValueError):
        return (1, Decimal(0), str(folio))


class StatementQueries(CatalogQueries):
    def __init__(self, db, allowed_tables, today=None):
        super().__init__(db, allowed_tables, today or business_today())

    def resolve(self, entity, term, exact_code=False):
        """The single party meant, or the answer that asks; same wording as the balance flow."""
        queries = (SupplierQueries if entity == SUPPLIER else CustomerQueries)(
            self.db, self.allowed, today=self.today
        )
        queries.operation = STATEMENT_OPERATION
        queries.sources = [CATALOGS[entity]]
        return queries._resolve(term, exact_code=exact_code)

    def _report(self, sql, params, limit=1):
        result = self.db.query_report(sql, params=params, row_limit=limit)
        return records(result), bool(result.get("has_more"))

    def _identity(self, entity, code):
        catalog = CATALOGS[entity]
        state = ", [ESTADO] AS estado" if entity == SUPPLIER else ""
        rows, more = self._report(
            "SELECT TOP 2 [NOMBRE] AS nombre, [SALDO ACTUAL] AS actual, "
            f"[SALDO_INICIAL] AS inicial{state} FROM {catalog} WHERE [CODIGO] = ?",
            (code,),
            2,
        )
        if len(rows) != 1 or more:
            raise BusinessError("No pude confirmar un registro único para ese código.")
        return rows[0]

    def _totals(self, source, when, code, start):
        """Before the period, overall, undated and without an amount, in one active scope."""
        rows, _ = self._report(
            f"SELECT SUM(CASE WHEN {when} < ? THEN [{source.amount_column}] ELSE 0 END) AS antes, "
            f"SUM([{source.amount_column}]) AS total, COUNT(*) - COUNT({when}) AS sin_fecha, "
            f"COUNT(*) - COUNT([{source.amount_column}]) AS sin_importe "
            f"FROM {source.table} WHERE [{source.party_column}] = ? AND {ACTIVE}",
            (start, code),
        )
        return rows[0]

    def _movements(self, source, when, code, start, until):
        lines = source.side != CREDIT
        columns = (
            "[CANTIDAD] AS cantidad, [CODIGO] AS codigo, [CONCEPTO] AS concepto, [PRECIO] AS precio"
            if lines
            else "[BANCO] AS codigo"
        )
        rows, more = self._report(
            f"SELECT [ID] AS id, [FOLIO] AS folio, {when} AS fecha, "
            f"[{source.amount_column}] AS importe, {columns} FROM {source.table} "
            f"WHERE [{source.party_column}] = ? AND {ACTIVE} AND {when} >= ? AND {when} < ? "
            "ORDER BY [ID]",
            (code, start, until),
            MAX_STATEMENT_ROWS + 1,
        )
        if more:
            raise _too_large()
        return rows

    def build(self, entity, code, start, end):
        if start > end:
            raise BusinessError("Indica un período válido: la fecha inicial va antes de la final.")
        sources = SOURCES[entity]
        self.operation, self.sources = STATEMENT_OPERATION, [CATALOGS[entity]]
        self.sources += [source.table for source in sources]
        catalog = self._schema(self.sources)
        self._require(
            catalog,
            CATALOGS[entity],
            ["CODIGO", "NOMBRE", "SALDO ACTUAL", "SALDO_INICIAL"],
            ["SALDO ACTUAL", "SALDO_INICIAL"],
        )
        identity = self._identity(entity, code)
        initial = decimal_value(identity["inicial"])
        opening, expected = initial, initial
        undated, missing = 0, 0
        pending = []
        until = end + timedelta(days=1)
        for source in sources:
            required = ["ID", "FOLIO", "FECHA", "ESTADO", source.party_column, source.amount_column]
            required += (
                ["CANTIDAD", "CODIGO", "CONCEPTO", "PRECIO"] if source.side != CREDIT else ["BANCO"]
            )
            self._require(catalog, source.table, required, [source.amount_column])
            when = date_expression(catalog[source.table]["FECHA"], "los movimientos")
            totals = self._totals(source, when, code, start)
            sign = 1 if source.side == CHARGE else -1
            opening += sign * decimal_value(totals["antes"])
            expected += sign * decimal_value(totals["total"])
            undated += int(totals["sin_fecha"] or 0)
            missing += int(totals["sin_importe"] or 0)
            pending += [(source, row) for row in self._movements(source, when, code, start, until)]
        if len(pending) > MAX_STATEMENT_ROWS:
            raise _too_large()
        pending.sort(
            key=lambda item: (
                str(item[1]["fecha"])[:10],
                SIDE_ORDER[item[0].side],
                _folio_order(item[1]["folio"]),
                _folio_order(item[1]["id"]),
            )
        )
        rows, balance = [], opening
        for source, row in pending:
            value = decimal_value(row["importe"])
            balance += value if source.side == CHARGE else -value
            rows.append(
                StatementRow(
                    kind=source.kind,
                    fecha=date.fromisoformat(str(row["fecha"])[:10]),
                    folio=str(row["folio"] or ""),
                    cantidad=decimal_value(row.get("cantidad")),
                    codigo=str(row.get("codigo") or ""),
                    concepto="ABONO" if source.side == CREDIT else str(row.get("concepto") or ""),
                    precio=decimal_value(row.get("precio")),
                    total=value if source.side == CHARGE else Decimal("0"),
                    abono=value if source.side == CREDIT else Decimal("0"),
                    devolucion=value if source.side == RETURN else Decimal("0"),
                    saldo=balance,
                )
            )
        current = None if identity["actual"] is None else Decimal(str(identity["actual"]))
        warnings = []
        if current is None:
            warnings.append("El saldo actual es desconocido; requiere revisión en el catálogo.")
        elif abs(expected - current) >= TOLERANCE:
            warnings.append(
                f"Los movimientos suman {amount(expected)} y no cuadran con el saldo actual "
                f"{amount(current)}; requiere revisión."
            )
        if undated:
            noun = "movimiento" if undated == 1 else "movimientos"
            warnings.append(f"Hay {undated} {noun} sin fecha válida; no aparecen en el reporte.")
        if missing:
            noun = "movimiento" if missing == 1 else "movimientos"
            warnings.append(f"Hay {missing} {noun} sin importe; se cuentan como cero.")
        return Statement(
            entity=entity,
            code=code,
            name=str(identity["nombre"] or ""),
            inactive=str(identity.get("estado") or "").strip().upper() == "INACTIVO",
            start=start,
            end=end,
            opening=opening,
            rows=tuple(rows),
            closing=balance,
            current=current,
            warnings=tuple(warnings),
        )
