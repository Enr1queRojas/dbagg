import unittest
from sqlglot.errors import ParseError
from dbagg.database.validation import validate_sql
from dbagg.database.connection import parse_odbc_options


class SQLTests(unittest.TestCase):
    def test_payment_dates_can_be_compared_as_dates_instead_of_text(self):
        sql = validate_sql(
            "SELECT MAX(TRY_CONVERT(date, [FECHA], 103)) AS ultima_fecha "
            "FROM proadel.COBRANZA_DATA WHERE ESTADO='ACTIVA' AND CLIENTE='TEST'",
            {"proadel.cobranza_data"},
        )
        self.assertIn("TRY_CONVERT(DATE, [FECHA], 103)", sql)
        self.assertIn("MAX(", sql)
        self.assertIn("TOP 50", sql)

    def test_combined_customer_status_and_date_filters_are_allowed(self):
        sql = validate_sql(
            "SELECT FECHA, IMPORTE FROM proadel.COBRANZA_DATA "
            "WHERE ESTADO='ACTIVA' AND (CLIENTE='TEST1' OR CLIENTE='TEST2') "
            "AND FECHA >= '2026-10-01' AND FECHA < '2026-11-01'",
            {"proadel.cobranza_data"},
        )
        self.assertIn("ESTADO = 'ACTIVA' AND", sql)
        self.assertIn("OR CLIENTE = 'TEST2'", sql)
        self.assertIn("FECHA < '2026-11-01'", sql)

    def test_select_is_bounded(self):
        sql = validate_sql(
            "SELECT TOP 1000 CLIENTE FROM proadel.vw_AlertasCobranza",
            {"proadel.vw_alertascobranza"},
        )
        self.assertIn("TOP 50", sql)
        self.assertNotIn("1000", sql)

    def test_rejects_writes_remote_and_unlisted_objects(self):
        for sql in [
            "DELETE FROM proadel.vw_AlertasCobranza",
            "SELECT * FROM proadel.vw_AlertasCobranza; DROP TABLE x",
            "SELECT * INTO backup FROM proadel.vw_AlertasCobranza",
            "SELECT * FROM otra.tabla",
            "SELECT * FROM otraDB.proadel.vw_AlertasCobranza",
            "SELECT * FROM OPENROWSET('x','y','z')",
            "SELECT dbo.secret(CLIENTE) FROM proadel.vw_AlertasCobranza",
            "SELECT * FROM proadel.vw_AlertasCobranza WITH (UPDLOCK)",
            "WITH c AS (SELECT * FROM proadel.vw_AlertasCobranza) SELECT * FROM c",
        ]:
            with self.subTest(sql=sql), self.assertRaises((ValueError, ParseError)):
                validate_sql(sql, {"proadel.vw_alertascobranza"})

    def test_odbc_password_cannot_override_tls(self):
        options = parse_odbc_options(
            "PWD={x;TrustServerCertificate=yes;z}}x};Encrypt=yes;TrustServerCertificate=no;"
        )
        self.assertEqual(options["trustservercertificate"], "no")
        with self.assertRaises(ValueError):
            parse_odbc_options("Encrypt=yes;Encrypt=no;")
