"""Conservative read-only T-SQL validation."""

import sqlglot
from sqlglot import exp
from dbagg.limits import MAX_ROWS


# Deterministic built-ins only. FORMAT/DATEPART share sqlglot's TIME_TO_STR and stay blocked.
ALLOWED_FUNCTIONS = frozenset(
    {
        "AND",
        "OR",
        "CASE",
        "IF",  # sqlglot's node for each CASE WHEN branch (and T-SQL IIF)
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "ABS",
        "ROUND",
        "COALESCE",
        "NULLIF",
        "CAST",
        "TRY_CAST",
        "CONVERT",
        "YEAR",
        "MONTH",
        "DAY",
        "DATE_DIFF",
        "DATE_ADD",
        "DATE_FROM_PARTS",
        "LAST_DAY",
        "TS_OR_DS_TO_DATE",
        "CURRENT_DATE",
        "CURRENT_TIMESTAMP",
        "LOWER",
        "UPPER",
        "TRIM",
    }
)
FUNCTION_ERROR = (
    "Función no autorizada. Permitidas: CASE, COUNT, SUM, AVG, MIN, MAX, ABS, ROUND, COALESCE, "
    "NULLIF, CAST, TRY_CAST, CONVERT, TRY_CONVERT, YEAR, MONTH, DAY, DATEDIFF, DATEADD, "
    "DATEFROMPARTS, EOMONTH, GETDATE, LOWER, UPPER, TRIM. Para agrupar por mes usa "
    "YEAR(fecha) y MONTH(fecha); no uses FORMAT ni DATEPART."
)


def _literal_limit(select):
    """Reject unsupported pagination instead of silently changing its meaning."""
    if select.args.get("offset") is not None:
        raise ValueError("OFFSET no está soportado; precisa el rango solicitado.")
    if select.args.get("options"):
        raise ValueError("Opciones de ejecución no autorizadas.")
    limit = select.args.get("limit")
    if limit is None:
        return None
    if not isinstance(limit, exp.Limit):
        raise ValueError("Solo se admite TOP con un entero positivo.")
    options = limit.args.get("limit_options")
    if options and any(options.args.values()):
        raise ValueError("TOP PERCENT y WITH TIES no están soportados.")
    value = limit.expression
    if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
        raise ValueError("TOP requiere un entero positivo literal.")
    count = int(value.this)
    if count <= 0:
        raise ValueError("TOP requiere un entero positivo literal.")
    return count


def _projected(select):
    return {e.alias_or_name.lower() for e in select.expressions if e.alias_or_name}


def _alias_reference(column):
    """T-SQL resolves a select alias only as a whole ORDER BY key or from a derived table.

    Anywhere else the name binds to a base column, so `SELECT 1 AS x, x` must not
    whitelist a column x that the catalogue excludes.
    """
    name = column.name.lower()
    select = column.find_ancestor(exp.Select)
    if select is None:
        return False
    if isinstance(column.parent, exp.Ordered) and name in _projected(select):
        return True
    sources = [select.args.get("from"), *select.args.get("joins", [])]
    return any(
        source is not None
        and isinstance(source.this, exp.Subquery)
        and isinstance(source.this.this, exp.Select)
        and name in _projected(source.this.this)
        for source in sources
    )


def _joins_on_columns(tree):
    """Every join needs ON a = b between columns of different objects: no Cartesian products."""
    for join in tree.find_all(exp.Join):
        condition = join.args.get("on")
        if condition is None or not any(
            isinstance(eq.left, exp.Column)
            and isinstance(eq.right, exp.Column)
            and eq.left.table
            and eq.right.table
            and eq.left.table.lower() != eq.right.table.lower()
            for eq in condition.find_all(exp.EQ)
        ):
            raise ValueError(
                "Cada JOIN necesita ON a.columna = b.columna entre objetos distintos; "
                "no se permiten productos cruzados."
            )


def _check_catalog(tree, tables, catalog):
    """Column and join policy from the source catalogue."""
    referenced = {f"{t.db}.{t.name}".lower() for t in tables}
    if not catalog.join_allowed(referenced):
        raise ValueError(
            "Combinar estos objetos no está autorizado; consulta cada uno por separado."
        )
    for node in tree.walk():
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            raise ValueError("Indica columnas explícitas; SELECT * no está permitido.")
    allowed = catalog.allowed_columns(referenced)
    for column in tree.find_all(exp.Column):
        if column.name.lower() not in allowed and not _alias_reference(column):
            raise ValueError("Columna no autorizada para este objeto.")


def validate_sql(sql, allowed_tables, catalog=None):
    """Conservative SQL subset; DB permissions are the final security boundary.

    With a catalogue, also restrict columns to the catalogued ones and joins to
    declared relationships.
    """
    if not isinstance(sql, str) or len(sql) > 8000:
        raise ValueError("Consulta inválida.")
    statements = sqlglot.parse(sql, read="tsql")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("Solo se permite una consulta SELECT.")
    tree = statements[0]
    forbidden = (
        exp.Into,
        exp.Command,
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Union,
        exp.Intersect,
        exp.Except,
    )
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise ValueError("Operación no permitida.")
    if tree.args.get("with") or any(isinstance(n, (exp.CTE, exp.Lock)) for n in tree.walk()):
        raise ValueError("CTE y bloqueos no permitidos.")
    if any(isinstance(n, (exp.Parameter, exp.Placeholder)) for n in tree.walk()):
        raise ValueError("Variables (@x, @@VERSION) y parámetros no permitidos.")
    tables = list(tree.find_all(exp.Table))
    if not tables:
        raise ValueError("La consulta debe usar una vista o tabla autorizada.")
    if len(tables) > 3:
        raise ValueError("Usa como máximo tres referencias a objetos por consulta.")
    _joins_on_columns(tree)
    for table in tables:
        if (
            table.catalog
            or not isinstance(table.this, exp.Identifier)
            or f"{table.db}.{table.name}".lower() not in allowed_tables
            or table.args.get("hints")
        ):
            raise ValueError("Tabla, destino remoto o hint no autorizado.")
    if catalog is not None:
        _check_catalog(tree, tables, catalog)
    for node in tree.walk():
        if isinstance(node, exp.Func) and node.sql_name() not in ALLOWED_FUNCTIONS:
            raise ValueError(FUNCTION_ERROR)
        if isinstance(node, exp.Dot):
            raise ValueError("Invocación o referencia compuesta no autorizada.")
    # Validate nested limits too, preserving their semantics inside aggregates.
    for select in tree.find_all(exp.Select):
        _literal_limit(select)
    requested = _literal_limit(tree)
    # Cap output without increasing a smaller requested TOP or removing OFFSET.
    for node in tree.walk():
        node.comments = None
    tree.set(
        "limit", exp.Limit(expression=exp.Literal.number(min(requested or MAX_ROWS, MAX_ROWS)))
    )
    return tree.sql(dialect="tsql")
