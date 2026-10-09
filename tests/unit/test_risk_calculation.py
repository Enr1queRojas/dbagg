import unittest
from datetime import date
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from dbagg.reporting import risk


class RiskCalculationTests(unittest.TestCase):
    def fixtures(self, balances=None):
        pagos = pd.DataFrame(
            [
                ["DEMO", date(2026, month, day), 100, None if index == 0 else 10]
                for index, (month, day) in enumerate(((8, 30), (9, 9), (9, 19), (9, 29)))
            ],
            columns=["cliente", "fecha", "monto", "intervalo"],
        )
        ultimo = pd.DataFrame([["DEMO", date(2026, 9, 29)]], columns=["cliente", "ultimo_pago"])
        primer = pd.DataFrame(columns=["cliente", "primer_cargo"])
        saldos = pd.DataFrame(balances or [["DEMO", 200]], columns=["cliente", "D"])
        calidad = pd.DataFrame(columns=["cliente", "fechas_invalidas"])
        return [pagos, ultimo, primer, saldos, calidad]

    def calculate(self, frames):
        return risk.calcular_desde_datos(*frames, hoy=date(2026, 10, 9)).set_index("cliente")

    def test_known_balance_keeps_formula_and_unknown_balances_are_never_green(self):
        frames = self.fixtures(
            [
                ["DEMO", 200],
                ["ZERO", 0],
                ["CREDIT", -50],
                ["NULL", None],
                ["INVALID", "bad"],
                ["INFINITY", np.inf],
            ]
        )
        result = self.calculate(frames)
        self.assertEqual(result.loc["DEMO", "score"], 2)
        self.assertEqual(result.loc["DEMO", "dias_liquidar"], 20)
        for client in ("ZERO", "CREDIT"):
            self.assertEqual(result.loc[client, "score"], 0)
            self.assertEqual(result.loc[client, "dias_liquidar"], 0)
        for client in ("NULL", "INVALID", "INFINITY"):
            with self.subTest(client=client):
                self.assertTrue(pd.isna(result.loc[client, "D"]))
                self.assertTrue(pd.isna(result.loc[client, "score"]))
                self.assertEqual(result.loc[client, "semaforo"], "sin_datos")
                self.assertIn("Saldo desconocido", result.loc[client, "motivo"])
        self.assertEqual(frames[3].loc[4, "D"], "bad")  # Inputs are not modified.

    def test_missing_reference_date_does_not_assume_client_is_current(self):
        frames = self.fixtures()
        frames[1]["ultimo_pago"] = pd.NaT
        result = self.calculate(frames)
        self.assertEqual(result.loc["DEMO", "semaforo"], "sin_datos")
        self.assertIn("fecha válida", result.loc["DEMO", "motivo"])

    def test_invalid_payment_dates_make_partial_history_explicit(self):
        frames = self.fixtures()
        frames[4] = pd.DataFrame([["DEMO", 1]], columns=["cliente", "fechas_invalidas"])
        result = self.calculate(frames)
        self.assertTrue(pd.isna(result.loc["DEMO", "score"]))
        self.assertIn("FECHA", result.loc["DEMO", "motivo"])

    def test_duplicate_or_missing_customer_keys_fail_without_guessing(self):
        for balances in ([["DEMO", 100], [" DEMO ", 100]], [[None, 100]], [["  ", 100]]):
            with self.subTest(balances=balances), self.assertRaises(ValueError):
                self.calculate(self.fixtures(balances))

    def test_no_payment_history_is_unscored_instead_of_zero(self):
        frames = self.fixtures()
        frames[0] = frames[0].iloc[:0]
        result = self.calculate(frames)
        self.assertEqual(result.loc["DEMO", "semaforo"], "sin_datos")
        self.assertTrue(pd.isna(result.loc["DEMO", "score"]))

    def test_empty_dataset_is_a_valid_empty_report(self):
        result = self.calculate([frame.iloc[:0] for frame in self.fixtures()])
        self.assertTrue(result.empty)

    def test_infinite_values_do_not_create_a_valid_risk(self):
        self.assertEqual(risk.valor_tipico([np.inf, np.nan, 10]), 10)
        self.assertEqual(risk.semaforo(np.inf), "sin_datos")

    def test_extraction_uses_payment_date_bounds_and_checks_invalid_dates(self):
        frames = self.fixtures()
        conn = Mock()
        with (
            patch("pyodbc.connect", return_value=conn) as connect,
            patch.object(
                risk, "_leer", side_effect=[frames[0], frames[1], frames[2], frames[4]]
            ) as read,
            patch.object(risk, "_leer_saldos", return_value=frames[3]),
        ):
            result = risk.calcular_score_riesgo("unused", hoy=date(2026, 10, 9))
        connect.assert_called_once_with("unused", readonly=True, timeout=10)
        conn.close.assert_called_once()
        self.assertEqual(conn.timeout, 15)
        self.assertEqual(result.iloc[0]["score"], 2)
        payments, last, first, quality = read.call_args_list
        self.assertEqual(payments.args[2], (date(2026, 10, 10), date(2025, 10, 9)))
        for call in (payments, last):
            self.assertIn("CONVERT(nvarchar(40), [FECHA], 103)", call.args[1])
            self.assertIn("TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(", call.args[1])
            self.assertNotIn("NOTE_DATE", call.args[1])
            self.assertIn("'ACTIVA'", call.args[1])
        self.assertEqual(last.args[2], (date(2026, 10, 10),))
        self.assertEqual(first.args[2], (date(2026, 10, 10),))
        self.assertIn("IS NULL", quality.args[1])


if __name__ == "__main__":
    unittest.main()
