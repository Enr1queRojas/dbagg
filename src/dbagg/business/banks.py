"""Confirmed bank-account contracts. Model arguments are values, never SQL fragments.

"Bancos" are every account in CATALOGO_BANCOS_DATA: bank accounts, people's cash, credit
lines and adjustment accounts. Balances come from [SALDO ACTUAL] and are never summed; the
owner confirmed that amounts are pesos and that a negative balance is shown as is.
Evidence (2026-11-09, 25 accounts): SALDO ACTUAL = SALDO INICIAL + DEPOSITOS - RETIROS, and
each concept column equals SUM(IMPORTE) of its source table's ESTADO='ACTIVA' rows by bank,
except maniobras (no bank column) and one cash-sales difference documented in
docs/business-queries.md.
"""

import json
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.customers import CatalogQueries, date_expression, decimal_value, like_literal
from dbagg.business.evidence import BusinessAnswer, BusinessError, amount, label, records
from dbagg.limits import MAX_REPORT_ROWS, MAX_ROWS

BANKS = "proadel.catalogo_bancos_data_v"
BANK_DETAIL = "proadel.catalogo_bancos_data"
BANK_OPERATIONS = ("saldo_banco", "saldos_bancos", "movimientos_banco")
DEPOSIT, WITHDRAWAL = "deposito", "retiro"
MATCH_LIMIT = 10
SCHEMA_BATCH = 5
MAX_ANSWER_CHARS = 2800
MAX_REPORT_MOVEMENTS = MAX_REPORT_ROWS - 1
REPORT_DATES_QUESTION = "¿De qué fechas quieres el reporte?"
BANK_NOT_FOUND = "No encontré una cuenta con ese nombre o código. ¿Puedes precisarlo?"
DUPLICATE_KEY = "El catálogo repite una clave de cuenta; no puedo confirmar su identidad."


@dataclass(frozen=True)
class MovementSource:
    table: str
    column: str
    kind: str
    concept: str
    detail: str
    by_folio: bool = False  # several item rows per document


MOVEMENT_SOURCES = (
    MovementSource(
        "proadel.contado_data",
        "BANCO",
        DEPOSIT,
        "Ventas de contado",
        "DEPOSITOS_CONTADO",
        by_folio=True,
    ),
    MovementSource("proadel.cobranza_data", "BANCO", DEPOSIT, "Cobranza", "DEPOSITOS_COBRANZA"),
    MovementSource("proadel.rentas_data", "BANCO", DEPOSIT, "Rentas", "DEPOSITOS_RENTAS"),
    MovementSource(
        "proadel.pago_prestamo_data",
        "BANCO",
        DEPOSIT,
        "Pagos de préstamos",
        "DEPOSITOS_PAGOS_PRESTAMOS",
    ),
    MovementSource(
        "proadel.transferencias_bancos_data",
        "BANCO DESTINO",
        DEPOSIT,
        "Transferencias recibidas",
        "DEPOSITOS_TRANSFERENCIAS_DESTINO",
    ),
    MovementSource(
        "proadel.insumo_contado_data",
        "BANCO",
        DEPOSIT,
        "Insumos de contado",
        "DEPOSITOS_INSUMOS_CONTADO",
    ),
    MovementSource(
        "proadel.abono_dinero_data",
        "BANCO",
        DEPOSIT,
        "Abonos de dinero",
        "DEPOSITOS_ABONOS_DINERO",
    ),
    MovementSource(
        "proadel.por_aplicar_data",
        "BANCO DESTINO",
        DEPOSIT,
        "Por aplicar (entradas)",
        "DEPOSITOS_POR_APLICAR",
    ),
    MovementSource(
        "proadel.pago_proveedor_data",
        "BANCO",
        WITHDRAWAL,
        "Pagos a proveedores",
        "RETIROS_PAGO_PROVEEDORES",
    ),
    MovementSource("proadel.gastos_data", "RETIRADO DE", WITHDRAWAL, "Gastos", "RETIROS_GASTOS"),
    MovementSource(
        "proadel.prestamo_data", "BANCO", WITHDRAWAL, "Préstamos otorgados", "RETIROS_PRESTAMOS"
    ),
    MovementSource(
        "proadel.pago_transporte_data",
        "BANCO",
        WITHDRAWAL,
        "Pagos de transporte",
        "RETIROS_PAGOS_TRANSPORTE",
    ),
    MovementSource(
        "proadel.transferencias_bancos_data",
        "BANCO ORIGEN",
        WITHDRAWAL,
        "Transferencias enviadas",
        "RETIROS_TRANSFERENCIAS_ORIGEN",
    ),
    MovementSource(
        "proadel.prestamo_dinero_data",
        "BANCO",
        WITHDRAWAL,
        "Préstamos de dinero",
        "RETIROS_PRESTAMO_DINERO",
    ),
    MovementSource(
        "proadel.por_aplicar_data",
        "BANCO ORIGEN",
        WITHDRAWAL,
        "Por aplicar (salidas)",
        "RETIROS_POR_APLICAR",
    ),
    MovementSource(
        "proadel.por_abonar_data", "BANCO DESTINO", WITHDRAWAL, "Por abonar", "RETIROS_PORABONAR"
    ),
)


@dataclass(frozen=True)
class BankMovement:
    fecha: date
    concepto: str
    folio: str
    deposito: Decimal
    retiro: Decimal


@dataclass(frozen=True)
class BankReport:
    """Every movement of one account in a period, for the PDF; built from data only."""

    code: str
    name: str
    inactive: bool
    start: date
    end: date
    deposits: Decimal
    withdrawals: Decimal
    concepts: tuple  # (concept, is_deposit, count, amount)
    rows: tuple
    current: Decimal | None
    warnings: tuple


class BankReportTooLarge(BusinessError):
    """More movements than a report may hold; a partial document would mislead."""


# Catalogue concepts without a per-account source table: shown in the balance, warned in
# movements.
UNSOURCED = {
    "DEPOSITOS_CLIENTES_NO_REGISTRADO": (DEPOSIT, "Clientes no registrados"),
    "RETIROS_MANIOBRAS": (WITHDRAWAL, "Maniobras"),
}
CONCEPTS = {s.detail: (s.kind, s.concept) for s in MOVEMENT_SOURCES} | UNSOURCED
DEPOSIT_COLUMNS = tuple(c for c, (kind, _) in CONCEPTS.items() if kind == DEPOSIT)
WITHDRAWAL_COLUMNS = tuple(c for c, (kind, _) in CONCEPTS.items() if kind == WITHDRAWAL)
MOVEMENT_TABLES = tuple(dict.fromkeys(s.table for s in MOVEMENT_SOURCES))
BANK_SOURCES = (BANKS, BANK_DETAIL) + MOVEMENT_TABLES
# Already catalogued for other domains; they keep their access.
SHARED_TABLES = frozenset(
    {
        "proadel.contado_data",
        "proadel.cobranza_data",
        "proadel.gastos_data",
        "proadel.pago_proveedor_data",
    }
)
# Only reachable through consultar_negocio.
BANK_PROTECTED_SOURCES = frozenset(BANK_SOURCES) - SHARED_TABLES


def validate_bank_context(context):
    """Local guidance cannot silently change the fixed, reviewed query contract."""
    topic = json.loads(context).get("topics", {}).get("bank_accounts", {})
    if (
        topic.get("status") != "confirmed_by_owner"
        or [s.lower() for s in topic.get("preferred_sources", [])] != [BANKS, BANK_DETAIL]
        or topic.get("confirmed_filters") != {"ESTADO": "ACTIVA"}
        or topic.get("columns", {}).get("current_balance") != "SALDO ACTUAL"
    ):
        raise BusinessError(
            "El contexto local difiere del contrato de consulta verificado; requiere revisión."
        )


def _normalized(term):
    text = "".join(c for c in unicodedata.normalize("NFKD", term) if not unicodedata.combining(c))
    return " ".join(text.upper().split())


def _inactive(row):
    return str(row.get("estado") or "").strip().upper() == "INACTIVO"


def _identity(row):
    name = " ".join(str(row.get("cuenta") or "").split())
    code = label(row["codigo"])
    return f"{code} ({name})" if name else code


class BankQueries(CatalogQueries):
    def __init__(self, db, allowed_tables, today=None):
        super().__init__(db, allowed_tables, today or business_today())
        self.report = None

    def _schema(self, sources):
        """The client describes at most five tables per call; movements need more."""
        catalog = {}
        for i in range(0, len(sources), SCHEMA_BATCH):
            catalog.update(super()._schema(sources[i : i + SCHEMA_BATCH]))
        return catalog

    def execute(self, operation, banco=None, period="todo", start=None, end=None, limit=5, **extra):
        if operation not in BANK_OPERATIONS or type(limit) is not int or not 1 <= limit <= 10:
            raise BusinessError(
                "Operación o cantidad no soportada; solicita entre uno y diez registros."
            )
        if extra.get("customer") is not None:
            raise BusinessError("Esta operación es por cuenta de banco, no por cliente.")
        if operation != "movimientos_banco" and (period != "todo" or start or end):
            raise BusinessError("Los saldos son actuales; no admiten un período.")
        if operation == "saldos_bancos" and banco is not None:
            raise BusinessError("La lista de saldos incluye todas las cuentas; omite la cuenta.")
        try:
            first, until = period_bounds(period, self.today, start, end)
        except (ValueError, TypeError, OverflowError):
            raise BusinessError(
                "Indica un período válido; para un rango usa inicio y fin en formato YYYY-MM-DD."
            ) from None
        self.operation = operation
        self.sources = [BANKS]
        if operation == "saldo_banco":
            self.sources.append(BANK_DETAIL)
        elif operation == "movimientos_banco":
            self.sources += [BANK_DETAIL, *MOVEMENT_TABLES]
        catalog = self._schema(self.sources)
        self._require(
            catalog,
            BANKS,
            [
                "CODIGO",
                "CUENTA BANCARIA",
                "SALDO ACTUAL",
                "DEPOSITOS",
                "RETIROS",
                "SALDO INICIAL",
                "ESTADO",
            ],
            ["SALDO ACTUAL", "DEPOSITOS", "RETIROS", "SALDO INICIAL"],
        )
        if operation == "saldos_bancos":
            return self._all_balances()
        self._require(catalog, BANK_DETAIL, ["CODIGO", *CONCEPTS], list(CONCEPTS))
        found = self._resolve(banco)
        if isinstance(found, BusinessAnswer):
            return found
        if operation == "saldo_banco":
            return self._balance(found)
        return self._movements(catalog, found, first, until)

    # -- identity ----------------------------------------------------------------------

    def _resolve(self, term):
        """An exact code or account name wins; otherwise partial matches, asking if several."""
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= 100:
            raise BusinessError("Indica el nombre o código de la cuenta que quieres consultar.")
        term = _normalized(term)
        columns = (
            "[CODIGO] AS codigo, [CUENTA BANCARIA] AS cuenta, [SALDO ACTUAL] AS saldo, "
            "[SALDO INICIAL] AS inicial, [DEPOSITOS] AS depositos, [RETIROS] AS retiros, "
            "[ESTADO] AS estado"
        )
        code = "UPPER(LTRIM(RTRIM([CODIGO])))"
        name = "UPPER(LTRIM(RTRIM([CUENTA BANCARIA])))"
        rows, more = self._query(
            f"SELECT TOP 2 {columns} FROM {BANKS} WHERE {code} = ? OR {name} = ? "
            f"ORDER BY CASE WHEN {code} = ? THEN 0 ELSE 1 END, [CODIGO]",
            (term, term, term),
            1,
        )
        if rows and (not more or _normalized(str(rows[0]["codigo"])) == term):
            return rows[0]
        pattern = like_literal(term)
        rows, more = self._query(
            f"SELECT TOP {MATCH_LIMIT + 1} {columns} FROM {BANKS} "
            f"WHERE {code} LIKE ? ESCAPE '!' OR {name} LIKE ? ESCAPE '!' ORDER BY [CODIGO]",
            (pattern, pattern),
            MATCH_LIMIT,
        )
        if not rows:
            return self._answer(BANK_NOT_FOUND, "needs_clarification")
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(DUPLICATE_KEY)
        if len(rows) == 1 and not more:
            return rows[0]
        choices = "\n".join(
            f"• {_identity(r)}{' · inactiva' if _inactive(r) else ''}" for r in rows
        )
        suffix = (
            "\nHay más coincidencias; precisa el nombre o código."
            if more
            else "\n¿Cuál quieres consultar?"
        )
        return self._answer("Encontré varias cuentas:\n" + choices + suffix, "needs_clarification")

    def _opening_lines(self, row):
        lines = [f"Cuenta {_identity(row)}"]
        if _inactive(row):
            lines.append("Cuenta inactiva en el catálogo.")
        return lines

    def _detail(self, row):
        selected = ", ".join(f"[{c}] AS [{c}]" for c in CONCEPTS)
        rows, more = self._query(
            f"SELECT TOP 2 {selected} FROM {BANK_DETAIL} WHERE [CODIGO] = ?",
            (row["codigo"],),
            1,
        )
        if len(rows) != 1 or more:
            raise BusinessError("No pude confirmar un desglose único para esa cuenta.")
        try:
            return {c: decimal_value(v) for c, v in rows[0].items()}
        except (InvalidOperation, ValueError):
            raise BusinessError("El desglose de la cuenta requiere revisión.") from None

    # -- operations --------------------------------------------------------------------

    def _balance(self, row):
        lines = self._opening_lines(row)
        if row["saldo"] is None:
            lines.append("Su saldo actual es desconocido; requiere revisión en el catálogo.")
            return self._answer("\n".join(lines), "unavailable")
        # Only the balance: the breakdown is checked, not shown; the PDF report has the detail.
        detail = self._detail(row)
        lines.append(f"Saldo actual: {amount(row['saldo'])}.")
        warnings = self._square_warnings(row, detail)
        lines += warnings
        lines.append("Importes en pesos.")
        return self._answer("\n".join(lines), "partial" if warnings else "answered")

    @staticmethod
    def _square_warnings(row, detail):
        deposits = sum((detail[c] for c in DEPOSIT_COLUMNS), Decimal("0"))
        withdrawals = sum((detail[c] for c in WITHDRAWAL_COLUMNS), Decimal("0"))
        balance = decimal_value(row["saldo"])
        expected = decimal_value(row["inicial"]) + deposits - withdrawals
        squared = (
            abs(deposits - decimal_value(row["depositos"])) < Decimal("0.01")
            and abs(withdrawals - decimal_value(row["retiros"])) < Decimal("0.01")
            and abs(expected - balance) < Decimal("0.01")
        )
        if squared:
            return []
        return [
            f"El desglose suma {amount(expected)} y no cuadra con el saldo actual "
            f"{amount(balance)}; requiere revisión."
        ]

    def _all_balances(self):
        rows, more = self._query(
            f"SELECT TOP {MAX_ROWS + 1} [CODIGO] AS codigo, [CUENTA BANCARIA] AS cuenta, "
            f"[SALDO ACTUAL] AS saldo, [ESTADO] AS estado FROM {BANKS} ORDER BY [CODIGO]",
            limit=MAX_ROWS,
        )
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(DUPLICATE_KEY)
        shown = [
            r
            for r in rows
            if not _inactive(r) or r["saldo"] is None or decimal_value(r["saldo"]) != 0
        ]
        if not shown:
            return self._answer("No encontré cuentas en el catálogo de bancos.", "empty")
        lines = ["Saldo actual de cada cuenta:"]
        unknown = False
        for row in shown:
            value = "saldo desconocido" if row["saldo"] is None else amount(row["saldo"])
            unknown |= row["saldo"] is None
            suffix = " · inactiva" if _inactive(row) else ""
            line = f"• {_identity(row)}: {value}{suffix}"
            if len("\n".join(lines + [line])) > MAX_ANSWER_CHARS - 200:
                more = True
                break
            lines.append(line)
        hidden = len(rows) - len(shown)
        if hidden:
            lines.append(
                "1 cuenta inactiva en cero no se muestra."
                if hidden == 1
                else f"{hidden} cuentas inactivas en cero no se muestran."
            )
        if more:
            lines.append("Hay más cuentas; precisa cuál quieres consultar.")
        if unknown:
            lines.append("Hay cuentas con saldo desconocido; requieren revisión en el catálogo.")
        lines.append("Saldos individuales; no se suman. Importes en pesos.")
        return self._answer("\n".join(lines), "partial" if more or unknown else "answered")

    def _movement_union(self, catalog, code, first, until):
        """UNION ALL of every source, with its parameters and the undated-rows check."""
        selects, params, undated, undated_params = [], (), [], ()
        for source in MOVEMENT_SOURCES:
            self._require(
                catalog,
                source.table,
                ["FOLIO", "FECHA", "IMPORTE", "ESTADO", source.column],
                ["IMPORTE"],
            )
            when = date_expression(catalog[source.table]["FECHA"], source.concept.lower())
            where = f"[{source.column}] = ? AND [ESTADO] = 'ACTIVA'"
            undated.append(f"(SELECT COUNT(*) - COUNT({when}) FROM {source.table} WHERE {where})")
            undated_params += (code,)
            scope, scope_params = where, (code,)
            if first:
                scope += f" AND {when} >= ? AND {when} < ?"
                scope_params += (first, until)
            else:
                scope += f" AND {when} IS NOT NULL"
            sign = "1" if source.kind == DEPOSIT else "-1"
            head = f"SELECT '{source.concept}' AS concepto, {sign} AS signo, [FOLIO] AS folio, "
            if source.by_folio:
                selects.append(
                    head + f"MIN({when}) AS fecha, SUM([IMPORTE]) AS importe "
                    f"FROM {source.table} WHERE {scope} GROUP BY [FOLIO]"
                )
            else:
                selects.append(
                    head + f"{when} AS fecha, [IMPORTE] AS importe "
                    f"FROM {source.table} WHERE {scope}"
                )
            params += scope_params
        undated_sql = "SELECT " + " + ".join(undated) + " AS invalid_dates"
        return " UNION ALL ".join(selects), params, undated_sql, undated_params

    def _movements(self, catalog, row, first, until):
        """Always a dated report; without dates the account is confirmed and they are asked."""
        if first is None:
            lines = self._opening_lines(row) + [REPORT_DATES_QUESTION]
            return self._answer("\n".join(lines), "needs_clarification")
        union, params, undated_sql, undated_params = self._movement_union(
            catalog, row["codigo"], first, until
        )
        quality, _ = self._query(undated_sql, undated_params, 1)
        invalid = int(quality[0]["invalid_dates"] or 0)
        totals, _ = self._query(
            "SELECT concepto, MIN(signo) AS signo, COUNT(*) AS cantidad, "
            "COUNT(*) - COUNT(importe) AS sin_importe, SUM(importe) AS importe "
            f"FROM ({union}) AS movimientos GROUP BY concepto ORDER BY concepto",
            params,
            len(MOVEMENT_SOURCES),
        )
        warnings = []
        if invalid:
            warnings.append("Hay movimientos activos sin fecha válida; no aparecen aquí.")
        detail = self._detail(row)
        for column, (_, concept) in UNSOURCED.items():
            if detail[column]:
                warnings.append(
                    f"{concept} ({amount(detail[column])} en total) se registran sin cuenta por "
                    "movimiento; no aparecen aquí aunque sí forman el saldo."
                )
        if not totals:
            period_text = f"Del {first:%d/%m/%Y} al {until - timedelta(days=1):%d/%m/%Y}."
            lines = self._opening_lines(row)
            lines.append(f"No encontré movimientos activos con fecha válida. {period_text}")
            return self._answer("\n".join(lines + warnings), "partial" if warnings else "empty")
        deposits = sum(
            (decimal_value(t["importe"]) for t in totals if int(t["signo"]) > 0), Decimal("0")
        )
        withdrawals = sum(
            (decimal_value(t["importe"]) for t in totals if int(t["signo"]) < 0), Decimal("0")
        )
        if any(int(t["sin_importe"]) for t in totals):
            warnings.append("Hay movimientos sin importe; los totales están incompletos.")
        return self._report(
            row, union, params, first, until, totals, deposits, withdrawals, warnings
        )

    def _report(self, row, union, params, first, until, totals, deposits, withdrawals, warnings):
        """A period's movements go in a PDF; the chat gets only the totals."""
        result = self.db.query_report(
            "SELECT concepto, signo, folio, fecha, importe "
            f"FROM ({union}) AS movimientos ORDER BY fecha, signo DESC, folio",
            params=params,
            row_limit=MAX_REPORT_ROWS,
        )
        found = records(result)
        if result.get("has_more") or len(found) > MAX_REPORT_MOVEMENTS:
            raise BankReportTooLarge(
                f"Ese período tiene más de {MAX_REPORT_MOVEMENTS:,} movimientos. "
                "Indica fechas más cortas."
            )
        movements = []
        for movement in found:
            value = decimal_value(movement["importe"])
            deposit = int(movement["signo"]) > 0
            movements.append(
                BankMovement(
                    fecha=date.fromisoformat(str(movement["fecha"])[:10]),
                    concepto=str(movement["concepto"]),
                    folio=label(movement["folio"]) if movement["folio"] is not None else "",
                    deposito=value if deposit else Decimal("0"),
                    retiro=Decimal("0") if deposit else value,
                )
            )
        end = until - timedelta(days=1)
        current = None if row["saldo"] is None else decimal_value(row["saldo"])
        self.report = BankReport(
            code=label(row["codigo"]),
            name=" ".join(str(row.get("cuenta") or "").split()),
            inactive=_inactive(row),
            start=first,
            end=end,
            deposits=deposits,
            withdrawals=withdrawals,
            concepts=tuple(
                (
                    t["concepto"],
                    int(t["signo"]) > 0,
                    int(t["cantidad"]),
                    decimal_value(t["importe"]),
                )
                for t in sorted(totals, key=lambda t: (-int(t["signo"]), t["concepto"]))
            ),
            rows=tuple(movements),
            current=current,
            warnings=tuple(warnings),
        )
        lines = self._opening_lines(row) + [
            f"Reporte del {first:%d/%m/%Y} al {end:%d/%m/%Y}.",
            f"Depósitos: {amount(deposits)} · Retiros: {amount(withdrawals)}.",
            f"Movimientos: {len(movements)}.",
            "Saldo actual desconocido; requiere revisión en el catálogo."
            if current is None
            else f"Saldo actual: {amount(current)}.",
            *warnings,
            "Importes en pesos.",
        ]
        return self._answer("\n".join(lines), "partial" if warnings else "answered")
