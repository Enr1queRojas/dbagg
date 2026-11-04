"""Confirmed supplier contracts. Model arguments are values, never SQL fragments."""

import json
from datetime import timedelta
from decimal import Decimal

from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.customers import CatalogQueries, date_expression, like_literal
from dbagg.business.evidence import BusinessAnswer, BusinessError, amount, display_label, label

SUPPLIERS = "proadel.catalogo_proveedores_data_v"
SUPPLIER_PAYMENTS = "proadel.pago_proveedor_data"
PURCHASES = "proadel.compras_data"
SUPPLIER_PROTECTED_SOURCES = frozenset({SUPPLIERS, SUPPLIER_PAYMENTS})
SUPPLIER_OPERATIONS = (
    "saldo",
    "saldos_coincidencias",
    "contar_coincidencias",
    "ranking_deuda",
    "pagos",
    "ultimo_pago",
    "total_pagos",
    "notas_pendientes",
    "ultima_nota",
    "movimientos",
)
SUPPLIER_ONLY_OPERATIONS = ("ultima_nota", "movimientos")
PERIOD_OPERATIONS = frozenset({"pagos", "total_pagos", "movimientos"})
MATCH_LIMIT = 10
MAX_ANSWER_CHARS = 2800
SUPPLIER_NOT_FOUND = "No encontré un proveedor con ese nombre o código. ¿Puedes precisarlo?"
SUPPLIER_MATCHES_NOT_FOUND = "No encontré proveedores con ese nombre o código. ¿Puedes precisarlo?"
DUPLICATE_KEY = "El catálogo repite una clave de proveedor; no puedo confirmar su identidad."


def validate_supplier_context(context):
    """Local guidance cannot silently change the fixed, reviewed query contracts."""
    topics = json.loads(context).get("topics", {})
    expected = {
        "supplier_balance": (
            SUPPLIERS,
            {
                "supplier_key": "CODIGO",
                "supplier_name": "NOMBRE",
                "current_balance": "SALDO ACTUAL",
            },
        ),
        "supplier_payments": (
            SUPPLIER_PAYMENTS,
            {
                "supplier_key": "PROVEEDOR",
                "amount": "IMPORTE",
                "date": "FECHA",
                "status": "ESTADO",
                "record_id": "ID",
                "receipt": "FOLIO",
            },
        ),
    }
    for topic, (source, columns) in expected.items():
        definition = topics.get(topic, {})
        actual = definition.get("columns", {})
        if (
            any(actual.get(key) != value for key, value in columns.items())
            or definition.get("status") != "confirmed_by_owner"
            or [s.lower() for s in definition.get("preferred_sources", [])] != [source]
        ):
            raise BusinessError(
                "El contexto local difiere del contrato de consulta verificado; requiere revisión."
            )
    if topics["supplier_payments"].get("confirmed_filters") != {"ESTADO": "ACTIVA"}:
        raise BusinessError(
            "El filtro local de pagos difiere del contrato de consulta verificado; requiere revisión."
        )


def _inactive(row):
    return str(row.get("estado") or "").strip().upper() == "INACTIVO"


def _heading(row):
    return f"Proveedor {label(row['nombre'])} (código {label(row['codigo'])})"


def _list_row(row):
    suffix = " · inactivo" if _inactive(row) else ""
    return f"{label(row['nombre'])} (código {label(row['codigo'])}){suffix}"


def _balance_phrase(value):
    number = Decimal(str(value))
    if number > 0:
        return f"le debemos {amount(number)}"
    if number < 0:
        return f"a nuestro favor {amount(-number)}"
    return "sin saldo pendiente"


class SupplierQueries(CatalogQueries):
    def __init__(self, db, allowed_tables, today=None):
        super().__init__(db, allowed_tables, today or business_today())

    def _matching_rows(self, columns, supplier, limit):
        """Partial matches on name or code; exact ones first so the limit cannot hide them."""
        pattern = like_literal(supplier)
        return self._query(
            f"SELECT TOP {limit + 1} {columns} FROM {SUPPLIERS} "
            "WHERE [CODIGO] LIKE ? ESCAPE '!' OR [NOMBRE] LIKE ? ESCAPE '!' "
            "ORDER BY CASE WHEN [CODIGO] = ? OR [NOMBRE] = ? THEN 0 ELSE 1 END, [CODIGO]",
            (pattern, pattern, supplier, supplier),
            limit,
        )

    @staticmethod
    def _term(supplier, purpose):
        if not isinstance(supplier, str) or not 1 <= len(supplier.strip()) <= 100:
            raise BusinessError(f"Indica el nombre o código del proveedor que quieres {purpose}.")
        return supplier.strip()

    def _resolve(self, supplier, exact_code):
        supplier = self._term(supplier, "consultar")
        columns = "[CODIGO] AS codigo, [NOMBRE] AS nombre, [ESTADO] AS estado"
        if exact_code:
            rows, more = self._query(
                f"SELECT TOP 6 {columns} FROM {SUPPLIERS} WHERE [CODIGO] = ? ORDER BY [CODIGO]",
                (supplier,),
                5,
            )
        else:
            rows, more = self._matching_rows(columns, supplier, 5)
        if not rows:
            return self._answer(SUPPLIER_NOT_FOUND, "needs_clarification")
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(DUPLICATE_KEY)
        if len(rows) != 1 or more:
            choices = "\n".join(f"• {_list_row(r)}" for r in rows)
            suffix = (
                "\nHay más coincidencias; precisa el nombre o código."
                if more
                else "\n¿Cuál quieres consultar?"
            )
            return self._answer(
                "Encontré varios proveedores:\n" + choices + suffix, "needs_clarification"
            )
        return rows[0]

    def execute(
        self,
        operation,
        supplier=None,
        period="todo",
        start=None,
        end=None,
        limit=5,
        codigo_exacto=False,
    ):
        if operation not in SUPPLIER_OPERATIONS or type(limit) is not int or not 1 <= limit <= 10:
            raise BusinessError(
                "Operación o cantidad no soportada; solicita entre uno y diez registros."
            )
        if operation not in PERIOD_OPERATIONS and (period != "todo" or start or end):
            raise BusinessError(
                "Esta operación no admite ese filtro de fechas; precisa el reporte solicitado."
            )
        if operation == "ranking_deuda" and supplier is not None:
            raise BusinessError(
                "El ranking corresponde al conjunto de proveedores, no a uno en particular."
            )
        self.operation = operation
        self.sources = [SUPPLIERS]
        if operation in {"pagos", "ultimo_pago", "total_pagos"}:
            self.sources.append(SUPPLIER_PAYMENTS)
        elif operation == "ultima_nota":
            self.sources.append(PURCHASES)
        elif operation in {"movimientos", "notas_pendientes"}:
            self.sources += [SUPPLIER_PAYMENTS, PURCHASES]
        try:
            first, until = period_bounds(period, self.today, start, end)
        except (ValueError, TypeError, OverflowError):
            raise BusinessError(
                "Indica un período válido; para un rango usa inicio y fin en formato YYYY-MM-DD."
            ) from None
        catalog = self._schema(self.sources)
        self._require(
            catalog, SUPPLIERS, ["CODIGO", "NOMBRE", "SALDO ACTUAL", "ESTADO"], ["SALDO ACTUAL"]
        )
        if operation == "ranking_deuda":
            return self._ranking(limit)
        if operation == "saldos_coincidencias":
            return self._matching_balances(supplier)
        if operation == "contar_coincidencias":
            return self._count_matches(supplier)
        found = self._resolve(supplier, codigo_exacto is True)
        if isinstance(found, BusinessAnswer):
            return found
        identity, more = self._query(
            "SELECT TOP 2 [CODIGO] AS codigo, [NOMBRE] AS nombre, [SALDO ACTUAL] AS importe, "
            f"[ESTADO] AS estado FROM {SUPPLIERS} WHERE [CODIGO] = ?",
            (found["codigo"],),
            2,
        )
        if len(identity) != 1 or more:
            raise BusinessError("No pude confirmar un registro único para ese proveedor.")
        row = identity[0]
        if operation == "saldo":
            return self._balance(row)
        if operation in {"pagos", "ultimo_pago", "total_pagos"}:
            return self._payments(catalog, row, first, until, limit)
        raise BusinessError("Operación no soportada.")

    def _payment_scope(self, catalog, row, first, until):
        """WHERE clause, parameters, date expression and undated count for active payments."""
        self._require(
            catalog,
            SUPPLIER_PAYMENTS,
            ["PROVEEDOR", "ESTADO", "FECHA", "IMPORTE", "ID", "FOLIO"],
            ["IMPORTE"],
        )
        expression = date_expression(catalog[SUPPLIER_PAYMENTS]["FECHA"], "pagos")
        where = "[PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA'"
        params = (row["codigo"],)
        quality, _ = self._query(
            f"SELECT COUNT(*) - COUNT({expression}) AS invalid_dates "
            f"FROM {SUPPLIER_PAYMENTS} WHERE {where}",
            params,
            1,
        )
        if first:
            where += f" AND {expression} >= ? AND {expression} < ?"
            params += (first, until)
        else:
            where += f" AND {expression} IS NOT NULL"
        return where, params, expression, int(quality[0]["invalid_dates"])

    def _payments(self, catalog, row, first, until, limit):
        where, params, expression, invalid = self._payment_scope(catalog, row, first, until)
        lines = self._opening_lines(row)
        if self.operation == "ultimo_pago" and invalid:
            lines.append(
                "Hay pagos activos sin fecha válida. No puedo confirmar cuál fue el último."
            )
            return self._answer("\n".join(lines), "unavailable")
        period_text = (
            f"Del {first:%d/%m/%Y} al {until - timedelta(days=1):%d/%m/%Y}."
            if first
            else "Historial completo de fechas válidas."
        )
        warnings = (
            ["Hay pagos activos sin fecha válida; no se pueden ubicar en el período."]
            if invalid
            else []
        )
        empty = lines + [f"No encontré pagos activos con fecha válida. {period_text}"] + warnings
        if self.operation == "total_pagos":
            totals, _ = self._query(
                "SELECT COUNT(*) AS cantidad, COUNT(*) - COUNT([IMPORTE]) AS missing_amounts, "
                f"SUM([IMPORTE]) AS importe FROM {SUPPLIER_PAYMENTS} WHERE {where}",
                params,
                1,
            )
            total = totals[0]
            if int(total["cantidad"]) == 0:
                return self._answer("\n".join(empty), "partial" if invalid else "empty")
            if int(total["missing_amounts"]):
                lines.append(f"Hay pagos sin importe; no puedo confirmar el total. {period_text}")
                return self._answer("\n".join(lines + warnings), "unavailable")
            lines.append(f"Total pagado: {amount(total['importe'])}. {period_text}")
            lines.append("Importe sin moneda confirmada.")
            return self._answer("\n".join(lines + warnings), "partial" if invalid else "answered")
        if self.operation == "ultimo_pago":
            where += (
                f" AND {expression} = (SELECT MAX({expression}) FROM {SUPPLIER_PAYMENTS} "
                "WHERE [PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA')"
            )
            params += (row["codigo"],)
        rows, more = self._query(
            f"SELECT TOP {limit + 1} [ID] AS id, [FOLIO] AS folio, {expression} AS fecha, "
            f"[IMPORTE] AS importe FROM {SUPPLIER_PAYMENTS} WHERE {where} "
            f"ORDER BY {expression} DESC, [ID] DESC",
            params,
            limit,
        )
        if not rows:
            return self._answer("\n".join(empty), "partial" if invalid else "empty")
        lines.append(
            "Pagos de la última fecha registrada:"
            if self.operation == "ultimo_pago"
            else "Pagos que le hicimos:"
        )
        shown = 0
        for payment in rows:
            value = (
                amount(payment["importe"])
                if payment["importe"] is not None
                else "importe no disponible"
            )
            folio = label(payment["folio"]) if payment["folio"] is not None else "no disponible"
            line = f"• {label(payment['fecha'])} · {value} · folio {folio}"
            if len("\n".join(lines + [line])) > MAX_ANSWER_CHARS:
                more = True
                break
            lines.append(line)
            shown += 1
        if self.operation == "ultimo_pago" and (len(rows) > 1 or more):
            lines.append("Comparten fecha; ID y folio no prueban cuál ocurrió después.")
        if more:
            lines.append(
                f"Muestro {shown} registros; hay más coincidencias. No es una lista completa."
            )
        if self.operation == "pagos":
            lines.append(period_text)
        lines.append("Importes sin moneda confirmada.")
        return self._answer(
            "\n".join(lines + warnings),
            "partial" if more or invalid or any(p["importe"] is None for p in rows) else "answered",
        )

    def _opening_lines(self, row):
        lines = [_heading(row)]
        if _inactive(row):
            lines.append("Proveedor inactivo en el catálogo.")
        return lines

    def _balance(self, row):
        lines = self._opening_lines(row)
        if row["importe"] is None:
            lines.append("Su saldo actual es desconocido; requiere revisión en el catálogo.")
            return self._answer("\n".join(lines), "unavailable")
        number = Decimal(str(row["importe"]))
        if number > 0:
            lines.append(f"Le debemos: {amount(number)}.")
        elif number < 0:
            lines.append(f"Saldo a nuestro favor: {amount(-number)}.")
        else:
            lines.append(f"Sin saldo pendiente: {amount(number)}.")
        lines.append("Importe sin moneda confirmada.")
        return self._answer("\n".join(lines))

    def _matching_balances(self, supplier):
        """Each matching supplier's current balance, listed separately and never summed."""
        supplier = self._term(supplier, "revisar")
        rows, more = self._matching_rows(
            "[CODIGO] AS codigo, [NOMBRE] AS nombre, [SALDO ACTUAL] AS importe, [ESTADO] AS estado",
            supplier,
            MATCH_LIMIT,
        )
        if not rows:
            return self._answer(SUPPLIER_MATCHES_NOT_FOUND, "needs_clarification")
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(DUPLICATE_KEY)
        lines = [f"Saldo actual de cada proveedor que coincide con «{label(supplier)}»:"]
        for i, row in enumerate(rows, 1):
            balance = (
                "saldo desconocido" if row["importe"] is None else _balance_phrase(row["importe"])
            )
            suffix = " · inactivo" if _inactive(row) else ""
            lines.append(
                f"{i}. {label(row['nombre'])} (código {label(row['codigo'])}): {balance}{suffix}"
            )
        unknown = any(r["importe"] is None for r in rows)
        if more:
            lines.append(f"Muestro {len(rows)} coincidencias; hay más. Precisa el nombre o código.")
        if unknown:
            lines.append(
                "Hay proveedores con saldo desconocido; requieren revisión en el catálogo."
            )
        lines.append("Saldos individuales; no se suman. Importes sin moneda confirmada.")
        return self._answer("\n".join(lines), "partial" if more or unknown else "answered")

    def _count_matches(self, supplier):
        """How many suppliers match, counted in SQL; names identify them, without balances."""
        supplier = self._term(supplier, "contar")
        pattern = like_literal(supplier)
        totals, _ = self._query(
            "SELECT COUNT(*) AS cantidad, COUNT(DISTINCT [CODIGO]) AS codigos "
            f"FROM {SUPPLIERS} WHERE [CODIGO] LIKE ? ESCAPE '!' OR [NOMBRE] LIKE ? ESCAPE '!'",
            (pattern, pattern),
            1,
        )
        total = int(totals[0]["cantidad"])
        if not total:
            return self._answer(SUPPLIER_MATCHES_NOT_FOUND, "needs_clarification")
        if int(totals[0]["codigos"]) != total:
            raise BusinessError(DUPLICATE_KEY)
        rows, _ = self._matching_rows(
            "[CODIGO] AS codigo, [NOMBRE] AS nombre, [ESTADO] AS estado", supplier, MATCH_LIMIT
        )
        subject = "1 proveedor que coincide" if total == 1 else f"{total} proveedores que coinciden"
        lines = [f"Hay {subject} con «{label(supplier)}» por nombre o código:"]
        lines += [f"{i}. {_list_row(row)}" for i, row in enumerate(rows, 1)]
        if total > len(rows):
            lines.append(f"Muestro {len(rows)} de {total}. Precisa el nombre para ver los demás.")
        return self._answer("\n".join(lines))

    def _ranking(self, limit):
        duplicates, _ = self._query(
            f"SELECT TOP 1 [CODIGO] AS codigo FROM {SUPPLIERS} "
            "GROUP BY [CODIGO] HAVING COUNT(*) > 1",
            limit=1,
        )
        if duplicates:
            raise BusinessError(DUPLICATE_KEY)
        quality, _ = self._query(
            f"SELECT COUNT(*) - COUNT([SALDO ACTUAL]) AS missing_balances FROM {SUPPLIERS}",
            limit=1,
        )
        rows, _ = self._query(
            f"SELECT TOP {limit} [CODIGO] AS codigo, [NOMBRE] AS nombre, "
            f"[SALDO ACTUAL] AS importe FROM {SUPPLIERS} WHERE [SALDO ACTUAL] > 0 "
            "ORDER BY [SALDO ACTUAL] DESC, [CODIGO] ASC",
            limit=limit,
        )
        missing = int(quality[0]["missing_balances"])
        warning = (
            "\nHay proveedores con saldo desconocido; el ranking solo incluye saldos conocidos."
            if missing
            else ""
        )
        if not rows:
            return self._answer(
                "No encontré proveedores a los que les debamos." + warning,
                "partial" if missing else "empty",
            )
        lines = [f"Proveedores a los que más les debemos (hasta {limit}):"]
        incomplete = False
        for i, row in enumerate(rows, 1):
            name, missing_name = display_label(row["nombre"], "nombre no disponible")
            code, missing_code = display_label(row["codigo"], "código no disponible")
            incomplete |= missing_name or missing_code
            lines.append(f"{i}. {name} ({code}): {amount(row['importe'])}")
        if incomplete:
            lines.append(
                "Hay registros con identificación incompleta; requieren revisión en el catálogo."
            )
        lines.append("Importes sin moneda confirmada. Los empates se ordenan por código.")
        return self._answer(
            "\n".join(lines) + warning, "partial" if missing or incomplete else "answered"
        )
