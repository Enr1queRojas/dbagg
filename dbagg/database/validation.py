"""Conservative read-only T-SQL validation."""

import sqlglot
from sqlglot import exp
from dbagg.limits import MAX_ROWS


def validate_sql(sql, allowed_tables):
    """Conservative SQL subset; DB permissions are the final security boundary."""
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
    tables = list(tree.find_all(exp.Table))
    if not tables:
        raise ValueError("La consulta debe usar una vista o tabla autorizada.")
    for table in tables:
        if (
            table.catalog
            or not isinstance(table.this, exp.Identifier)
            or f"{table.db}.{table.name}".lower() not in allowed_tables
            or table.args.get("hints")
        ):
            raise ValueError("Tabla, destino remoto o hint no autorizado.")
    for node in tree.walk():
        if isinstance(node, exp.Func) and node.sql_name() not in {
            "AND",
            "OR",
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
            "CURRENT_DATE",
            "CURRENT_TIMESTAMP",
            "LOWER",
            "UPPER",
            "TRIM",
        }:
            raise ValueError("Función no autorizada.")
        if isinstance(node, exp.Dot):
            raise ValueError("Invocación o referencia compuesta no autorizada.")
    # Strip comments and replace any model-selected TOP/OFFSET with our own bound.
    for node in tree.walk():
        node.comments = None
    tree.set("offset", None)
    tree.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
    return tree.sql(dialect="tsql")
