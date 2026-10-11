"""Bounded SQL Server catalogue and query access."""

import json
import pyodbc
from dbagg.limits import MAX_ROWS, MAX_CELL_CHARS, MAX_RESULT_CHARS


class Database:
    def __init__(self, settings):
        self.settings = settings

    def _connect(self):
        conn = pyodbc.connect(self.settings.connection, readonly=True, timeout=10, autocommit=True)
        conn.timeout = 15
        return conn

    def schema(self, tables=None):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            catalog = []
            selected = (
                self.settings.tables if tables is None else frozenset(t.lower() for t in tables)
            )
            if (
                not selected
                or not selected.issubset(self.settings.tables)
                or (tables is not None and len(selected) > 5)
            ):
                raise ValueError("Selecciona entre una y cinco tablas autorizadas.")
            for qualified in sorted(selected):
                schema, name = qualified.split(".")
                cursor.execute(
                    "SELECT c.COLUMN_NAME, c.DATA_TYPE, CONVERT(nvarchar(1000), ep.value) "
                    "FROM INFORMATION_SCHEMA.COLUMNS c "
                    "LEFT JOIN sys.schemas s ON s.name=c.TABLE_SCHEMA "
                    "LEFT JOIN sys.objects o ON o.schema_id=s.schema_id AND o.name=c.TABLE_NAME "
                    "LEFT JOIN sys.columns sc ON sc.object_id=o.object_id AND sc.name=c.COLUMN_NAME "
                    "LEFT JOIN sys.extended_properties ep ON ep.class=1 AND ep.major_id=o.object_id "
                    "AND ep.minor_id=sc.column_id AND ep.name='MS_Description' "
                    "WHERE c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ? ORDER BY c.ORDINAL_POSITION",
                    schema,
                    name,
                )
                columns = [
                    {"name": r[0], "type": r[1], "description": r[2]} for r in cursor.fetchall()
                ]
                if not columns:
                    raise ValueError("Objeto autorizado no visible en el catálogo SQL.")
                catalog.append({"table": qualified, "columns": columns})
            payload = json.dumps(catalog, ensure_ascii=False)
            if len(payload) > 24000:
                raise ValueError("Catálogo demasiado grande; reducir vistas autorizadas.")
            return payload
        finally:
            conn.close()

    def query(self, sql, params=(), row_limit=MAX_ROWS):
        if type(row_limit) is not int or not 1 <= row_limit <= MAX_ROWS:
            raise ValueError("Límite de filas inválido.")
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, *params)
            columns = [str(c[0])[:100] for c in cursor.description]
            fetched = cursor.fetchmany(row_limit + 1)
            truncated = any(
                v is not None and len(str(v)) > MAX_CELL_CHARS
                for row in fetched[:row_limit]
                for v in row
            )
            rows = [
                [None if v is None else str(v)[:MAX_CELL_CHARS] for v in row]
                for row in fetched[:row_limit]
            ]
            result = {
                "columns": columns,
                "rows": [],
                "row_limit": row_limit,
                "has_more": len(fetched) > row_limit,
                "values_may_be_truncated": truncated,
            }
            for row in rows:
                candidate = dict(result, rows=result["rows"] + [row])
                if len(json.dumps(candidate, ensure_ascii=False)) > MAX_RESULT_CHARS:
                    result["response_size_limit_reached"] = True
                    break
                result = candidate
            return result
        finally:
            conn.close()
