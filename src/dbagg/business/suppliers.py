"""Confirmed supplier contracts. Model arguments are values, never SQL fragments."""

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.customers import CatalogQueries, date_expression, decimal_value, like_literal
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
NOTE_LIMIT = 50
NOTE_ITEM_LIMIT = 10
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


def _day(value):
    return str(value or "sin fecha")[:10]


def _number(value):
    return "no disponible" if value is None else amount(value)


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
        self._require(
            catalog,
            PURCHASES,
            ["PROVEEDOR", "ESTADO", "FECHA", "FOLIO", "IMPORTE", "CONCEPTO", "CANTIDAD", "PRECIO"],
            ["IMPORTE"],
        )
        if operation == "ultima_nota":
            return self._last_note(catalog, row)
        if operation == "movimientos":
            return self._movements(catalog, row, first, until, limit)
        return self._pending_notes(catalog, row)

    def _last_note(self, catalog, row):
        when = date_expression(catalog[PURCHASES]["FECHA"], "compras")
        code = row["codigo"]
        active = "[PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA'"
        lines = self._opening_lines(row)
        notes, _ = self._query(
            f"SELECT TOP 2 [FOLIO] AS folio, MAX({when}) AS fecha, SUM([IMPORTE]) AS importe "
            f"FROM {PURCHASES} WHERE {active} GROUP BY [FOLIO] "
            f"HAVING MAX({when}) IS NOT NULL ORDER BY MAX({when}) DESC, [FOLIO] DESC",
            (code,),
            2,
        )
        if not notes:
            lines.append("No encontré notas de compra activas con fecha válida.")
            return self._answer("\n".join(lines), "empty")
        note = notes[0]
        items, more = self._query(
            f"SELECT TOP {NOTE_ITEM_LIMIT + 1} [CONCEPTO] AS concepto, [CANTIDAD] AS cantidad, "
            f"[PRECIO] AS precio, [IMPORTE] AS importe FROM {PURCHASES} "
            f"WHERE {active} AND [FOLIO] = ? ORDER BY [IMPORTE] DESC",
            (code, note["folio"]),
            NOTE_ITEM_LIMIT,
        )
        lines.append(f"Última nota: folio {label(note['folio'])} · {_day(note['fecha'])}.")
        lines.append(f"Importe de la nota: {amount(note['importe'])}.")
        for item in items:
            concept = display_label(item["concepto"], "concepto no disponible")[0]
            lines.append(
                f"• {concept} · {_number(item['cantidad'])} × {_number(item['precio'])} "
                f"· {_number(item['importe'])}"
            )
        warnings = []
        if more:
            warnings.append(f"Muestro {NOTE_ITEM_LIMIT} partidas; el importe sí incluye todas.")
        if len(notes) > 1 and notes[1]["fecha"] == note["fecha"]:
            warnings.append("Comparten fecha; el folio no prueba cuál ocurrió después.")
        lines += warnings
        lines.append("Importes sin moneda confirmada.")
        return self._answer("\n".join(lines), "partial" if more else "answered")

    def _movements(self, catalog, row, first, until, limit):
        bought = date_expression(catalog[PURCHASES]["FECHA"], "compras")
        paid = date_expression(catalog[SUPPLIER_PAYMENTS]["FECHA"], "pagos")
        code = row["codigo"]
        active = "[PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA'"
        quality, _ = self._query(
            f"SELECT (SELECT COUNT(*) - COUNT({bought}) FROM {PURCHASES} WHERE {active}) "
            f"+ (SELECT COUNT(*) - COUNT({paid}) FROM {SUPPLIER_PAYMENTS} WHERE {active}) "
            "AS invalid_dates",
            (code, code),
            1,
        )
        invalid = int(quality[0]["invalid_dates"])
        purchase_where, payment_where = active, active
        purchase_params, payment_params = (code,), (code,)
        if first:
            purchase_where += f" AND {bought} >= ? AND {bought} < ?"
            payment_where += f" AND {paid} >= ? AND {paid} < ?"
            purchase_params += (first, until)
            payment_params += (first, until)
        else:
            purchase_where += f" AND {bought} IS NOT NULL"
            payment_where += f" AND {paid} IS NOT NULL"
        rows, more = self._query(
            f"SELECT TOP {limit + 1} tipo, folio, fecha, importe FROM ("
            f"SELECT 'compra' AS tipo, [FOLIO] AS folio, MIN({bought}) AS fecha, "
            f"SUM([IMPORTE]) AS importe FROM {PURCHASES} WHERE {purchase_where} GROUP BY [FOLIO] "
            f"UNION ALL SELECT 'pago' AS tipo, [FOLIO] AS folio, {paid} AS fecha, "
            f"[IMPORTE] AS importe FROM {SUPPLIER_PAYMENTS} WHERE {payment_where}"
            ") AS movimientos ORDER BY fecha DESC, folio DESC",
            purchase_params + payment_params,
            limit,
        )
        lines = self._opening_lines(row)
        period_text = (
            f"Del {first:%d/%m/%Y} al {until - timedelta(days=1):%d/%m/%Y}."
            if first
            else "Historial completo de fechas válidas."
        )
        warning = ["Hay movimientos activos sin fecha válida; no aparecen aquí."] if invalid else []
        if not rows:
            lines.append(f"No encontré movimientos activos con fecha válida. {period_text}")
            return self._answer("\n".join(lines + warning), "partial" if invalid else "empty")
        for movement in rows:
            sign = "+" if movement["tipo"] == "compra" else "−"
            value = (
                f"{sign}{amount(movement['importe'])}"
                if movement["importe"] is not None
                else "importe no disponible"
            )
            line = (
                f"• {_day(movement['fecha'])} · {movement['tipo']} · "
                f"folio {label(movement['folio'])} · {value}"
            )
            if len("\n".join(lines + [line])) > MAX_ANSWER_CHARS:
                more = True
                break
            lines.append(line)
        lines.append(period_text)
        if row["importe"] is None:
            lines.append("Saldo actual desconocido; requiere revisión en el catálogo.")
        else:
            lines.append(f"Saldo actual: {amount(row['importe'])}.")
        if more:
            lines.append("Hay más movimientos. No es un estado de cuenta completo.")
        lines.append("Importes sin moneda confirmada.")
        unknown = any(m["importe"] is None for m in rows)
        return self._answer(
            "\n".join(lines + warning), "partial" if more or invalid or unknown else "answered"
        )

    def _pending_notes(self, catalog, row):
        """Purchase notes that make up what we owe, allocating active payments FIFO."""
        self._require(catalog, SUPPLIERS, ["SALDO_INICIAL"], ["SALDO_INICIAL"])
        self._require(catalog, SUPPLIER_PAYMENTS, ["PROVEEDOR", "ESTADO", "IMPORTE"], ["IMPORTE"])
        if row["importe"] is None:
            raise BusinessError("El saldo actual del proveedor es desconocido; requiere revisión.")
        when = date_expression(catalog[PURCHASES]["FECHA"], "compras")
        code = row["codigo"]
        active = "[PROVEEDOR] = ? AND [ESTADO] = 'ACTIVA'"
        totals, _ = self._query(
            f"SELECT (SELECT [SALDO_INICIAL] FROM {SUPPLIERS} WHERE [CODIGO] = ?) AS inicial, "
            f"(SELECT SUM([IMPORTE]) FROM {PURCHASES} WHERE {active}) AS cargos, "
            f"(SELECT SUM([IMPORTE]) FROM {SUPPLIER_PAYMENTS} WHERE {active}) AS pagos, "
            f"(SELECT COUNT(*) - COUNT({when}) FROM {PURCHASES} WHERE {active}) AS sin_fecha, "
            f"(SELECT COUNT(*) FROM (SELECT [FOLIO] AS f FROM {PURCHASES} WHERE {active} "
            "GROUP BY [FOLIO] HAVING SUM([IMPORTE]) <= 0) AS n) AS no_positivas",
            (code,) * 5,
            1,
        )
        try:
            total = totals[0]
            balance = Decimal(str(row["importe"]))
            initial, charges = decimal_value(total["inicial"]), decimal_value(total["cargos"])
            credits = decimal_value(total["pagos"])
        except (InvalidOperation, ValueError, TypeError, IndexError):
            raise BusinessError("Los importes del proveedor requieren revisión.") from None
        expected = initial + charges - credits
        squared = abs(expected - balance) < Decimal("0.01")
        warnings = []
        if not squared:
            warnings.append(
                f"Los movimientos suman {amount(expected)} y no cuadran con el saldo actual "
                f"{amount(balance)}; la lista requiere revisión."
            )
        if int(total["sin_fecha"] or 0):
            warnings.append("Hay partidas sin fecha válida; el orden de antigüedad es aproximado.")
        if int(total["no_positivas"] or 0):
            warnings.append("Hay notas con importe cero o negativo; la asignación es aproximada.")
        lines = self._opening_lines(row)
        if balance <= 0:
            lines.append(f"No tiene notas pendientes de pago: {_balance_phrase(balance)}.")
            return self._answer("\n".join(lines + warnings), "partial" if warnings else "answered")
        applied = credits - initial
        notes, more = self._query(
            f"SELECT TOP {NOTE_LIMIT + 1} folio, fecha, importe, acumulado FROM ("
            f"SELECT [FOLIO] AS folio, MIN({when}) AS fecha, SUM([IMPORTE]) AS importe, "
            f"SUM(SUM([IMPORTE])) OVER (ORDER BY MIN({when}), [FOLIO] ROWS UNBOUNDED PRECEDING) "
            f"AS acumulado FROM {PURCHASES} WHERE {active} GROUP BY [FOLIO]) AS notas "
            "WHERE acumulado > ? ORDER BY fecha, folio",
            (code, float(applied)),
            NOTE_LIMIT,
        )
        lines.append("Notas pendientes de pago (de la más antigua a la más reciente):")
        initial_pending = max(Decimal("0"), initial - credits)
        if initial_pending:
            lines.append(f"• Saldo inicial pendiente: {amount(initial_pending)}")
        shown = 0
        for note in notes:
            note_total = decimal_value(note["importe"])
            pending = min(note_total, decimal_value(note["acumulado"]) - applied)
            if pending < Decimal("0.005"):
                continue
            noted = _day(note["fecha"])
            try:
                age = f" · {(self.today - date.fromisoformat(noted)).days} días"
            except ValueError:
                noted, age = "sin fecha", ""
            partial = " (parcial)" if pending < note_total else ""
            line = (
                f"• {noted} · folio {label(note['folio'])} · nota {amount(note_total)} · "
                f"pendiente {amount(pending)}{partial}{age}"
            )
            if len("\n".join(lines + [line])) > MAX_ANSWER_CHARS:
                more = True
                break
            lines.append(line)
            shown += 1
        if more:
            warnings.append(
                f"Muestro las {shown} notas pendientes más antiguas; hay más. El total sí "
                "incluye todas."
            )
        lines.append(f"Total pendiente: {amount(max(Decimal('0'), expected))}.")
        if squared:
            lines.append("Cuadra con el saldo actual.")
        lines += warnings
        lines.append(
            "Método: los pagos activos liquidan primero el saldo inicial y luego las notas más "
            "antiguas (FIFO). Importes sin moneda confirmada."
        )
        return self._answer("\n".join(lines), "partial" if warnings else "answered")

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
            line = f"• {_day(payment['fecha'])} · {value} · folio {folio}"
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
