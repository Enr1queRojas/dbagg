"""Confirmed customer contracts. Model arguments are values, never SQL fragments."""

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.evidence import (
    BusinessAnswer,
    BusinessError,
    amount,
    label,
    records,
    display_label,
)

CUSTOMERS = "proadel.catalogo_clientes_data_v"
PAYMENTS = "proadel.cobranza_data"
CREDITS = "proadel.credito_data"
RETURNS = "proadel.dev_venta_data"
PROTECTED_SOURCES = frozenset({CUSTOMERS, PAYMENTS})
OPERATIONS = (
    "saldo",
    "saldos_coincidencias",
    "ranking_deuda",
    "pagos",
    "ultimo_pago",
    "total_pagos",
    "notas_pendientes",
)
NOTE_LIMIT = 50
MATCH_LIMIT = 10
CUSTOMER_NOT_FOUND = "No encontré un cliente con ese nombre o código. ¿Puedes precisarlo?"
MATCHES_NOT_FOUND = "No encontré clientes con ese nombre o código. ¿Puedes precisarlo?"
NOT_FOUND_ANSWERS = frozenset({CUSTOMER_NOT_FOUND, MATCHES_NOT_FOUND})
NUMERIC_TYPES = {
    "decimal",
    "numeric",
    "money",
    "smallmoney",
    "float",
    "real",
    "int",
    "bigint",
    "smallint",
    "tinyint",
}


def like_literal(value):
    return (
        "%"
        + value.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")
        + "%"
    )


def date_expression(column_type, subject):
    """SQL for FECHA by its real type; text dates are dd/MM/yyyy (style 103)."""
    if column_type in {"date", "datetime", "datetime2", "smalldatetime"}:
        return "[FECHA]"
    if column_type in {"varchar", "nvarchar", "char", "nchar"}:
        return "TRY_CONVERT(datetime2, NULLIF(LTRIM(RTRIM([FECHA])), ''), 103)"
    raise BusinessError(f"El tipo de fecha de {subject} requiere una definición verificada.")


def decimal_value(value):
    return Decimal("0") if value is None else Decimal(str(value))


def validate_context(context, operation):
    """Local guidance cannot silently change the fixed, reviewed query contracts."""
    topics = json.loads(context).get("topics", {})
    expected = {
        "customer_balance": (
            CUSTOMERS,
            {
                "customer_key": "CODIGO",
                "customer_name": "NOMBRE",
                "current_balance": "SALDO ACTUAL",
            },
        ),
    }
    if operation in {"pagos", "ultimo_pago", "total_pagos", "notas_pendientes"}:
        expected["collections"] = (
            PAYMENTS,
            {
                "customer_key": "CLIENTE",
                "amount": "IMPORTE",
                "date": "FECHA",
                "status": "ESTADO",
                "record_id": "ID",
                "receipt": "FOLIO",
            },
        )
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
    if "collections" in expected and topics["collections"].get("confirmed_filters") != {
        "ESTADO": "ACTIVA"
    }:
        raise BusinessError(
            "El filtro local de pagos difiere del contrato verificado; requiere revisión."
        )


class CustomerQueries:
    def __init__(self, db, allowed_tables, today=None):
        self.db = db
        self.allowed = set(allowed_tables)
        self.today = today or business_today()

    def _schema(self, sources):
        if not set(sources).issubset(self.allowed):
            raise BusinessError("La fuente necesaria para esta consulta no está autorizada.")
        catalog = json.loads(self.db.schema(sources))
        return {
            item["table"].lower(): {c["name"].upper(): c["type"].lower() for c in item["columns"]}
            for item in catalog
        }

    def _require(self, catalog, table, fields, numeric):
        columns = catalog.get(table, {})
        if not set(fields).issubset(columns) or any(
            columns.get(c) not in NUMERIC_TYPES for c in numeric
        ):
            raise BusinessError(
                "La fuente no coincide con las columnas y tipos verificados; requiere revisión."
            )

    def _query(self, sql, params=(), limit=10):
        result = self.db.query(sql, params=params, row_limit=limit)
        return records(result), bool(result.get("has_more"))

    def _answer(self, text, status="answered"):
        return BusinessAnswer.make(text, status, self.operation, self.sources)

    def _resolve(self, customer):
        if not isinstance(customer, str) or not 1 <= len(customer.strip()) <= 100:
            raise BusinessError("Indica el nombre o código del cliente que quieres consultar.")
        customer = customer.strip()
        base = f"SELECT TOP 6 [CODIGO] AS codigo, [NOMBRE] AS nombre FROM {CUSTOMERS} "
        rows, more = self._query(
            base + "WHERE [CODIGO] = ? OR [NOMBRE] = ? ORDER BY [CODIGO]", (customer, customer), 5
        )
        if not rows:
            pattern = like_literal(customer)
            rows, more = self._query(
                base
                + "WHERE [CODIGO] LIKE ? ESCAPE '!' OR [NOMBRE] LIKE ? ESCAPE '!' ORDER BY [CODIGO]",
                (pattern, pattern),
                5,
            )
        if not rows:
            return self._answer(CUSTOMER_NOT_FOUND, "needs_clarification")
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(
                "El catálogo repite una clave de cliente; no puedo confirmar su identidad."
            )
        if len(rows) != 1 or more:
            choices = "\n".join(
                f"• {label(r['nombre'])} (código {label(r['codigo'])})" for r in rows
            )
            suffix = (
                "\nHay más coincidencias; precisa el nombre o código."
                if more
                else "\n¿Cuál quieres consultar?"
            )
            return self._answer(
                "Encontré varios clientes:\n" + choices + suffix, "needs_clarification"
            )
        label(rows[0]["codigo"])
        label(rows[0]["nombre"])
        return rows[0]

    def execute(self, operation, customer=None, period="todo", start=None, end=None, limit=5):
        if operation not in OPERATIONS or type(limit) is not int or not 1 <= limit <= 10:
            raise BusinessError(
                "Operación o cantidad no soportada; solicita entre uno y diez registros."
            )
        if operation in {
            "saldo",
            "saldos_coincidencias",
            "ranking_deuda",
            "ultimo_pago",
            "notas_pendientes",
        } and (period != "todo" or start or end):
            raise BusinessError(
                "Esta operación no admite ese filtro de fechas; precisa el reporte solicitado."
            )
        if operation == "ranking_deuda" and customer is not None:
            raise BusinessError(
                "El ranking corresponde al conjunto de clientes, no a un cliente particular."
            )
        self.operation = operation
        self.sources = [CUSTOMERS] + (
            [PAYMENTS]
            if operation in {"pagos", "ultimo_pago", "total_pagos"}
            else [PAYMENTS, CREDITS, RETURNS]
            if operation == "notas_pendientes"
            else []
        )
        try:
            first, until = period_bounds(period, self.today, start, end)
        except (ValueError, TypeError, OverflowError):
            raise BusinessError(
                "Indica un período válido; para un rango usa inicio y fin en formato YYYY-MM-DD."
            ) from None
        catalog = self._schema(self.sources)
        self._require(catalog, CUSTOMERS, ["CODIGO", "NOMBRE", "SALDO ACTUAL"], ["SALDO ACTUAL"])
        if operation == "ranking_deuda":
            return self._ranking(limit)
        if operation == "saldos_coincidencias":
            return self._matching_balances(customer)
        client = self._resolve(customer)
        if isinstance(client, BusinessAnswer):
            return client
        # Recheck uniqueness by code; a name match could hide duplicate codes under other names.
        identity, more = self._query(
            f"SELECT TOP 2 [CODIGO] AS codigo, [NOMBRE] AS nombre, [SALDO ACTUAL] AS importe FROM {CUSTOMERS} WHERE [CODIGO] = ?",
            (client["codigo"],),
            2,
        )
        if len(identity) != 1 or more:
            raise BusinessError("No pude confirmar un registro único para ese cliente.")
        client = identity[0]
        heading = f"{label(client['nombre'])} (código {label(client['codigo'])})"
        if operation == "notas_pendientes":
            return self._pending_notes(catalog, client, heading)
        if operation == "saldo":
            return self._answer(
                f"Saldo actual de {heading}: {amount(client['importe'])}.\nImporte sin moneda confirmada."
            )
        self._require(
            catalog, PAYMENTS, ["CLIENTE", "ESTADO", "FECHA", "IMPORTE", "ID", "FOLIO"], ["IMPORTE"]
        )
        expression = date_expression(catalog[PAYMENTS]["FECHA"], "pagos")
        where = "[CLIENTE] = ? AND [ESTADO] = 'ACTIVA'"
        params = (client["codigo"],)
        quality, _ = self._query(
            f"SELECT COUNT(*) - COUNT({expression}) AS invalid_dates FROM {PAYMENTS} WHERE {where}",
            params,
            1,
        )
        invalid_dates = int(quality[0]["invalid_dates"])
        if operation == "ultimo_pago" and invalid_dates:
            return self._answer(
                f"Hay pagos activos de {heading} sin fecha válida. No puedo confirmar cuál fue el último.",
                "unavailable",
            )
        if first:
            where += f" AND {expression} >= ? AND {expression} < ?"
            params += (first, until)
        else:
            where += f" AND {expression} IS NOT NULL"
        period_text = (
            f"Del {first:%d/%m/%Y} al {until - timedelta(days=1):%d/%m/%Y}."
            if first
            else "Historial completo de fechas válidas."
        )
        warning = (
            "\nHay pagos activos sin fecha válida; no se pueden ubicar en el período."
            if invalid_dates
            else ""
        )
        if operation == "total_pagos":
            totals, _ = self._query(
                f"SELECT COUNT(*) AS cantidad, COUNT(*) - COUNT([IMPORTE]) AS missing_amounts, SUM([IMPORTE]) AS importe FROM {PAYMENTS} WHERE {where}",
                params,
                1,
            )
            total = totals[0]
            if int(total["cantidad"]) == 0:
                return self._answer(
                    f"No encontré pagos activos con fecha válida para {heading}. {period_text}"
                    + warning,
                    "partial" if invalid_dates else "empty",
                )
            missing = int(total["missing_amounts"])
            if missing:
                return self._answer(
                    f"Hay pagos de {heading} sin importe; no puedo confirmar el total. {period_text}"
                    + warning,
                    "unavailable",
                )
            return self._answer(
                f"Total de pagos activos de {heading}: {amount(total['importe'])}. {period_text}\nImporte sin moneda confirmada."
                + warning,
                "partial" if invalid_dates else "answered",
            )
        if operation == "ultimo_pago":
            where += f" AND {expression} = (SELECT MAX({expression}) FROM {PAYMENTS} WHERE [CLIENTE] = ? AND [ESTADO] = 'ACTIVA')"
            params += (client["codigo"],)
        rows, more = self._query(
            f"SELECT TOP {limit + 1} [ID] AS id, [FOLIO] AS folio, {expression} AS fecha, [IMPORTE] AS importe FROM {PAYMENTS} WHERE {where} ORDER BY {expression} DESC, [ID] DESC",
            params,
            limit,
        )
        if not rows:
            return self._answer(
                f"No encontré pagos activos con fecha válida para {heading}. {period_text}"
                + warning,
                "partial" if invalid_dates else "empty",
            )
        title = (
            "Pagos de la última fecha registrada" if operation == "ultimo_pago" else "Pagos activos"
        )
        lines = [f"{title} de {heading}:"]
        shown = 0
        for row in rows:
            value = (
                amount(row["importe"]) if row["importe"] is not None else "importe no disponible"
            )
            line = f"• {label(row['fecha'])} · {value} · folio {label(row['folio']) if row['folio'] is not None else 'no disponible'} · ID {label(row['id'])}"
            if len("\n".join(lines + [line])) > 2800:
                more = True
                break
            lines.append(line)
            shown += 1
        if operation == "ultimo_pago" and (len(rows) > 1 or more):
            lines.append("Comparten fecha; ID y folio no prueban cuál ocurrió después.")
        if more:
            lines.append(
                f"Muestro {shown} registros; hay más coincidencias. No es una lista completa."
            )
        if operation == "pagos":
            lines.append(period_text)
        lines.append("Importes sin moneda confirmada.")
        return self._answer(
            "\n".join(lines) + warning,
            "partial"
            if more or invalid_dates or any(r["importe"] is None for r in rows)
            else "answered",
        )

    def _pending_notes(self, catalog, client, heading):
        """Credit notes that make up the current balance, allocating credits FIFO.

        Active payments and returns settle the opening balance first, then notes by date
        and folio. The remaining amounts must add up to [SALDO ACTUAL]; a mismatch is shown.
        """
        self._require(catalog, CUSTOMERS, ["SALDO_INICIAL"], ["SALDO_INICIAL"])
        self._require(catalog, PAYMENTS, ["CLIENTE", "ESTADO", "IMPORTE"], ["IMPORTE"])
        self._require(catalog, CREDITS, ["CLIENTE", "ESTADO", "FOLIO", "FECHA", "TOTAL"], ["TOTAL"])
        self._require(catalog, RETURNS, ["CLIENTE", "ESTADO", "IMPORTE"], ["IMPORTE"])
        when = date_expression(catalog[CREDITS]["FECHA"], "notas de crédito")
        code = client["codigo"]
        active = "[CLIENTE] = ? AND [ESTADO] = 'ACTIVA'"
        totals, _ = self._query(
            f"SELECT (SELECT [SALDO_INICIAL] FROM {CUSTOMERS} WHERE [CODIGO] = ?) AS inicial, "
            f"(SELECT SUM([TOTAL]) FROM {CREDITS} WHERE {active}) AS cargos, "
            f"(SELECT SUM([IMPORTE]) FROM {PAYMENTS} WHERE {active}) AS pagos, "
            f"(SELECT SUM([IMPORTE]) FROM {RETURNS} WHERE {active}) AS devoluciones, "
            f"(SELECT COUNT(*) - COUNT({when}) FROM {CREDITS} WHERE {active}) AS sin_fecha, "
            f"(SELECT COUNT(*) FROM (SELECT [FOLIO] AS f FROM {CREDITS} WHERE {active} "
            f"GROUP BY [FOLIO] HAVING SUM([TOTAL]) <= 0) AS n) AS no_positivas",
            (code,) * 6,
            1,
        )
        if client["importe"] is None:
            raise BusinessError("El saldo actual del cliente es desconocido; requiere revisión.")
        try:
            row = totals[0]
            balance = Decimal(str(client["importe"]))
            initial, charges = decimal_value(row["inicial"]), decimal_value(row["cargos"])
            credits = decimal_value(row["pagos"]) + decimal_value(row["devoluciones"])
        except (InvalidOperation, ValueError, TypeError, IndexError):
            raise BusinessError("Los importes del cliente requieren revisión.") from None
        expected = initial + charges - credits
        squared = abs(expected - balance) < Decimal("0.01")
        warnings = []
        if not squared:
            warnings.append(
                f"Los movimientos suman {amount(expected)} y no cuadran con el saldo actual "
                f"{amount(balance)}; la lista requiere revisión."
            )
        if int(row["sin_fecha"] or 0):
            warnings.append("Hay partidas sin fecha válida; el orden de antigüedad es aproximado.")
        if int(row["no_positivas"] or 0):
            warnings.append("Hay notas con importe cero o negativo; la asignación es aproximada.")
        if balance <= 0:
            state = "a favor del cliente" if balance < 0 else "en cero"
            return self._answer(
                "\n".join(
                    [
                        f"{heading} no tiene notas de crédito pendientes: su saldo actual está "
                        f"{state} ({amount(balance)})."
                    ]
                    + warnings
                ),
                "partial" if warnings else "answered",
            )
        # Credits left after settling the opening balance (negative opening adds credit).
        applied = credits - initial
        notes, more = self._query(
            f"SELECT TOP {NOTE_LIMIT + 1} folio, fecha, importe, acumulado FROM ("
            f"SELECT [FOLIO] AS folio, MIN({when}) AS fecha, SUM([TOTAL]) AS importe, "
            f"SUM(SUM([TOTAL])) OVER (ORDER BY MIN({when}), [FOLIO] ROWS UNBOUNDED PRECEDING) "
            f"AS acumulado FROM {CREDITS} WHERE {active} GROUP BY [FOLIO]) AS notas "
            f"WHERE acumulado > ? ORDER BY fecha, folio",
            (code, float(applied)),
            NOTE_LIMIT,
        )
        lines = [f"Notas de crédito pendientes de {heading} (de la más antigua a la más reciente):"]
        initial_pending = max(Decimal("0"), initial - credits)
        if initial_pending:
            lines.append(f"• Saldo inicial pendiente: {amount(initial_pending)}")
        shown = 0
        for note in notes:
            total = decimal_value(note["importe"])
            pending = min(total, decimal_value(note["acumulado"]) - applied)
            if pending < Decimal("0.005"):
                continue
            noted = str(note["fecha"] or "")[:10]
            try:
                age = f" · {(self.today - date.fromisoformat(noted)).days} días"
            except ValueError:
                noted, age = "sin fecha", ""
            partial = " (parcial)" if pending < total else ""
            line = (
                f"• {noted} · folio {label(note['folio'])} · nota {amount(total)} · "
                f"pendiente {amount(pending)}{partial}{age}"
            )
            if len("\n".join(lines + [line])) > 2800:
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
        lines.extend(warnings)
        lines.append(
            "Método: pagos y devoluciones activos liquidan primero el saldo inicial y luego "
            "las notas más antiguas (FIFO). Importes sin moneda confirmada."
        )
        return self._answer("\n".join(lines), "partial" if warnings else "answered")

    def _matching_balances(self, customer):
        """Each matching customer's current balance, listed separately and never summed."""
        if not isinstance(customer, str) or not 1 <= len(customer.strip()) <= 100:
            raise BusinessError("Indica el nombre o código de los clientes que quieres revisar.")
        customer = customer.strip()
        base = (
            f"SELECT TOP {MATCH_LIMIT + 1} [CODIGO] AS codigo, [NOMBRE] AS nombre, "
            f"[SALDO ACTUAL] AS importe FROM {CUSTOMERS} "
        )
        rows, more = self._query(
            base + "WHERE [CODIGO] = ? OR [NOMBRE] = ? ORDER BY [CODIGO]",
            (customer, customer),
            MATCH_LIMIT,
        )
        if not rows:
            pattern = like_literal(customer)
            rows, more = self._query(
                base
                + "WHERE [CODIGO] LIKE ? ESCAPE '!' OR [NOMBRE] LIKE ? ESCAPE '!' ORDER BY [CODIGO]",
                (pattern, pattern),
                MATCH_LIMIT,
            )
        if not rows:
            return self._answer(MATCHES_NOT_FOUND, "needs_clarification")
        codes = [r["codigo"] for r in rows]
        if len(set(codes)) != len(codes):
            raise BusinessError(
                "El catálogo repite una clave de cliente; no puedo confirmar su identidad."
            )
        lines = [f"Saldo actual de cada cliente que coincide con «{label(customer)}»:"]
        for i, row in enumerate(rows, 1):
            name = label(row["nombre"])
            code = label(row["codigo"])
            balance = "saldo desconocido" if row["importe"] is None else amount(row["importe"])
            lines.append(f"{i}. {name} (código {code}): {balance}")
        unknown = any(r["importe"] is None for r in rows)
        if more:
            lines.append(f"Muestro {len(rows)} coincidencias; hay más. Precisa el nombre o código.")
        if unknown:
            lines.append("Hay clientes con saldo desconocido; requieren revisión en el catálogo.")
        lines.append("Saldos individuales; no se suman. Importes sin moneda confirmada.")
        return self._answer("\n".join(lines), "partial" if more or unknown else "answered")

    def _ranking(self, limit):
        duplicates, _ = self._query(
            f"SELECT TOP 1 [CODIGO] AS codigo FROM {CUSTOMERS} GROUP BY [CODIGO] HAVING COUNT(*) > 1",
            limit=1,
        )
        if duplicates:
            raise BusinessError(
                "El catálogo repite claves; no puedo confirmar un ranking por cliente."
            )
        quality, _ = self._query(
            f"SELECT COUNT(*) - COUNT([SALDO ACTUAL]) AS missing_balances FROM {CUSTOMERS}", limit=1
        )
        rows, _ = self._query(
            f"SELECT TOP {limit} [CODIGO] AS codigo, [NOMBRE] AS nombre, [SALDO ACTUAL] AS importe FROM {CUSTOMERS} WHERE [SALDO ACTUAL] > 0 ORDER BY [SALDO ACTUAL] DESC, [CODIGO] ASC",
            limit=limit,
        )
        missing = int(quality[0]["missing_balances"])
        warning = (
            "\nHay clientes con saldo desconocido; el ranking solo incluye saldos conocidos."
            if missing
            else ""
        )
        if not rows:
            return self._answer(
                "No encontré saldos positivos conocidos." + warning,
                "partial" if missing else "empty",
            )
        lines = [f"Clientes con mayor saldo pendiente (hasta {limit}):"]
        incomplete_identity = False
        shown = 0
        for i, row in enumerate(rows, 1):
            name, missing_name = display_label(row["nombre"], "nombre no disponible")
            code, missing_code = display_label(row["codigo"], "código no disponible")
            incomplete_identity |= missing_name or missing_code
            line = f"{i}. {name} ({code}): {amount(row['importe'])}"
            if len("\n".join(lines + [line])) > 2800:
                break
            lines.append(line)
            shown += 1
        if incomplete_identity:
            lines.append(
                "Hay registros con identificación incompleta; requieren revisión en el catálogo."
            )
        if shown < len(rows):
            lines.append(
                f"Muestro {shown} de {len(rows)} registros del ranking por espacio; la lista es parcial."
            )
        lines.append("Importes sin moneda confirmada. Los empates se ordenan por código.")
        return self._answer(
            "\n".join(lines) + warning,
            "partial" if missing or incomplete_identity or shown < len(rows) else "answered",
        )
