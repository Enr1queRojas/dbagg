"""Confirmed product contracts: stock and sales in tons, using a reviewed kg-per-unit table.

The ERP UNIDAD column is unreliable, so tons come only from units.json. Products missing
from it are listed instead of being converted by guess.
"""

import json
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from dbagg.business.calendar import business_today, period_bounds
from dbagg.business.customers import NUMERIC_TYPES, date_expression, decimal_value, like_literal
from dbagg.business.evidence import BusinessAnswer, BusinessError, amount, label, table_lines
from dbagg.limits import MAX_ROWS

STOCK = "proadel.almacenes_disponibles_v"
CREDITS = "proadel.credito_data"
CASH = "proadel.contado_data"
RETURNS = "proadel.dev_venta_data"
SALES = ((CREDITS, 1), (CASH, 1), (RETURNS, -1))
PRODUCT_OPERATIONS = ("existencias", "ventas")
COMPARISONS = ("periodo_anterior", "anio_anterior")
UNITS = json.loads(Path(__file__).with_name("units.json").read_text(encoding="utf-8"))
KG_PER_UNIT = {code: Decimal(str(kg)) for code, kg in UNITS["kg_por_unidad"].items()}
NOT_GRAIN = frozenset(UNITS["no_grano"])
EXCLUDED_FROM_SALES = frozenset(UNITS["excluir_de_ventas"])
FAMILIES = {name: tuple(codes) for name, codes in UNITS["familias"].items()}


def product_filter(product, name_column):
    """SQL condition and params for a product word: accents removed, so «maíz» matches MAIZ.

    A family word (maíz, frijol) also matches its reviewed codes, because varieties such
    as SALTILLO or BILL Z do not carry the word «frijol» in their name.
    """
    if product is None:
        return "", ()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", str(product)) if not unicodedata.combining(c)
    )
    text = " ".join(text.upper().split())
    if not re.fullmatch(r"[A-Z0-9 ]{2,40}", text):
        raise BusinessError("Indica el producto con una palabra, por ejemplo maíz o frijol.")
    pattern = like_literal(text)
    condition = f"UPPER([{name_column}]) LIKE ? ESCAPE '!' OR UPPER([CODIGO]) LIKE ? ESCAPE '!'"
    codes = FAMILIES.get(text.removesuffix("ES").removesuffix("S"), ())
    if codes:
        condition += f" OR LTRIM(RTRIM([CODIGO])) IN ({', '.join('?' * len(codes))})"
    return f" AND ({condition})", (pattern, pattern, *codes)


def tons(quantity, code):
    kg = KG_PER_UNIT.get(code)
    return None if kg is None else decimal_value(quantity) * kg / 1000


def missing_note(codes):
    if not codes:
        return []
    return [
        "Sin conversión a toneladas (falta en la tabla de unidades): "
        + ", ".join(sorted(codes)[:10])
        + "."
    ]


def next_month(first):
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 de febrero
        return day.replace(year=day.year - 1, day=28)


def comparison_periods(period, today, start=None, end=None, compare="periodo_anterior"):
    """Requested period first, then what it is compared against, as (name, first, until).

    A period reaching past today is cut at today, so a month in progress is compared with
    the same days of the other month plus that whole month. A range covering one calendar
    month is compared with the previous calendar month, not with the previous 30 days.
    """
    if compare not in COMPARISONS:
        raise BusinessError("Compara contra el periodo anterior o contra el año anterior.")
    if period == "todo":
        period = "este_mes"
    try:
        first, until = period_bounds(period, today, start, end)
    except (ValueError, TypeError, OverflowError):
        raise BusinessError(
            "Indica un período válido; para un rango usa inicio y fin en formato YYYY-MM-DD."
        ) from None
    if period == "este_mes":
        until = next_month(first)
    whole_month = first.day == 1 and until == next_month(first)
    tomorrow = today + timedelta(days=1)
    if first >= tomorrow:
        raise BusinessError("Ese período todavía no empieza; no hay ventas que comparar.")
    until = min(until, tomorrow)
    in_progress = whole_month and until < next_month(first)
    if compare == "anio_anterior":
        rows = [
            ("actual", first, until),
            ("mismo periodo año anterior", year_before(first), year_before(until)),
        ]
        if in_progress:
            whole = (year_before(first), year_before(next_month(first)))
            rows.append(("mes completo año anterior", *whole))
        return rows
    if whole_month:
        previous_first = (first - timedelta(days=1)).replace(day=1)
        if not in_progress:
            return [("actual", first, until), ("mes anterior", previous_first, first)]
        last_day = until - timedelta(days=1)
        month_end = first - timedelta(days=1)
        same_day = previous_first.replace(day=min(last_day.day, month_end.day))
        return [
            ("actual", first, until),
            ("mismo periodo mes anterior", previous_first, same_day + timedelta(days=1)),
            ("mes anterior completo", previous_first, first),
        ]
    length = until - first
    return [("actual", first, until), ("periodo anterior", first - length, first)]


def span(first, until):
    return f"{first:%d/%m/%Y}-{until - timedelta(days=1):%d/%m/%Y}"


class ProductQueries:
    def __init__(self, db, allowed_tables, today=None):
        self.db = db
        self.allowed = set(allowed_tables)
        self.today = today or business_today()

    def _columns(self, sources, required, numeric):
        if not set(sources).issubset(self.allowed):
            raise BusinessError("La fuente necesaria para esta consulta no está autorizada.")
        catalog = {
            item["table"].lower(): {c["name"].upper(): c["type"].lower() for c in item["columns"]}
            for item in json.loads(self.db.schema(sources))
        }
        for source in sources:
            columns = catalog.get(source, {})
            if not set(required).issubset(columns) or any(
                columns.get(c) not in NUMERIC_TYPES for c in numeric
            ):
                raise BusinessError(
                    "La fuente no coincide con las columnas y tipos verificados; requiere revisión."
                )
        return catalog

    def _rows(self, sql, params):
        result = self.db.query(sql, params=params, row_limit=MAX_ROWS)
        if result.get("has_more"):
            raise BusinessError("Hay demasiados productos; precisa cuál quieres consultar.")
        return [dict(zip(result["columns"], row)) for row in result["rows"]]

    def execute(
        self,
        operation,
        producto=None,
        period="todo",
        start=None,
        end=None,
        comparar="periodo_anterior",
        **extra,
    ):
        if operation not in PRODUCT_OPERATIONS:
            raise BusinessError("Operación de productos no soportada.")
        if extra.get("customer") is not None:
            raise BusinessError("Esta operación es por producto, no por cliente.")
        if operation == "existencias":
            if period != "todo" or start or end or comparar != "periodo_anterior":
                raise BusinessError("Las existencias son actuales; no admiten un período.")
            return self._stock(producto)
        return self._sales(producto, period, start, end, comparar)

    def _stock(self, product):
        self._columns([STOCK], ["CODIGO", "DESCRIPCION", "EXISTENCIAS"], ["EXISTENCIAS"])
        condition, params = product_filter(product, "DESCRIPCION")
        rows = self._rows(
            "SELECT [CODIGO] AS codigo, [DESCRIPCION] AS descripcion, [EXISTENCIAS] AS existencias "
            f"FROM {STOCK} WHERE [EXISTENCIAS] <> 0{condition} ORDER BY [EXISTENCIAS] DESC",
            params,
        )
        rows = [r for r in rows if label(r["codigo"]) not in NOT_GRAIN]
        subject = f"de {label(product)}" if product else "de granos"
        if not rows:
            return BusinessAnswer.make(
                f"No hay existencias distintas de cero {subject}.", "empty", "existencias", [STOCK]
            )
        cells, missing, total = [], set(), Decimal(0)
        for row in rows:
            code = label(row["codigo"])
            weight = tons(row["existencias"], code)
            if weight is None:
                missing.add(code)
            else:
                total += weight
            cells.append(
                [
                    label(row["descripcion"] or code, 30),
                    amount(row["existencias"]),
                    str(KG_PER_UNIT.get(code, "?")),
                    amount(weight) if weight is not None else "sin dato",
                ]
            )
        lines = [
            f"Existencias actuales {subject}:",
            "```",
            *table_lines(["producto", "existencia", "kg/u", "toneladas"], cells),
            "```",
            f"Total: {amount(total)} t.",
            "Existencia en la unidad de cada producto; kg/u según la tabla de unidades revisada.",
            *missing_note(missing),
        ]
        return BusinessAnswer.make("\n".join(lines), "answered", "existencias", [STOCK])

    def _sales(self, product, period, start, end, compare):
        sources = [table for table, _ in SALES]
        catalog = self._columns(
            sources,
            ["CODIGO", "CONCEPTO", "CANTIDAD", "IMPORTE", "ESTADO", "FECHA"],
            ["CANTIDAD", "IMPORTE"],
        )
        condition, product_params = product_filter(product, "CONCEPTO")
        missing, totals, undated = set(), [], 0
        for name, first, until in comparison_periods(period, self.today, start, end, compare):
            pesos, weight = Decimal(0), Decimal(0)
            for table, sign in SALES:
                expression = date_expression(catalog[table]["FECHA"], "ventas")
                where = f"[ESTADO] = 'ACTIVA' AND {expression} >= ? AND {expression} < ?"
                where += condition
                params = (first, until, *product_params)
                for row in self._rows(
                    "SELECT [CODIGO] AS codigo, SUM([CANTIDAD]) AS cantidad, "
                    f"SUM([IMPORTE]) AS importe FROM {table} WHERE {where} GROUP BY [CODIGO]",
                    params,
                ):
                    code = label(row["codigo"])
                    if code in EXCLUDED_FROM_SALES:
                        continue
                    pesos += sign * decimal_value(row["importe"])
                    if code in NOT_GRAIN:
                        continue
                    converted = tons(row["cantidad"], code)
                    if converted is None:
                        missing.add(code)
                    else:
                        weight += sign * converted
            totals.append((name, first, until, pesos, weight))
        for table in sources:
            expression = date_expression(catalog[table]["FECHA"], "ventas")
            check = self._rows(
                f"SELECT COUNT(*) - COUNT({expression}) AS invalid_dates FROM {table} "
                "WHERE [ESTADO] = 'ACTIVA'",
                (),
            )
            undated += int(check[0]["invalid_dates"] or 0)
        cells = [
            [name, span(first, until), amount(pesos), amount(weight)]
            for name, first, until, pesos, weight in totals
        ]
        subject = f" de {label(product)}" if product else ""
        lines = [
            f"Ventas{subject} (crédito + contado − devoluciones, partidas activas, sin préstamos):",
            "```",
            *table_lines(["periodo", "fechas", "pesos MXN", "toneladas"], cells),
            "```",
        ]
        current, previous = totals[0], totals[1]
        changes = [
            f"{(current[i] - previous[i]) / abs(previous[i]) * 100:+.1f} % {unit}"
            for i, unit in ((3, "en pesos"), (4, "en toneladas"))
            if previous[i]
        ]
        if changes:
            lines.append(f"Variación contra {previous[0]}: " + ", ".join(changes) + ".")
        lines.append("Toneladas solo de granos con conversión confirmada.")
        lines += missing_note(missing)
        if undated:
            lines.append(
                "Hay partidas activas sin fecha válida; no se pueden ubicar en un período."
            )
        return BusinessAnswer.make(
            "\n".join(lines), "partial" if undated else "answered", "ventas", sources
        )
