import json
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from score_riesgo import generar_html, semaforo

COLUMNS = ['cliente', 'D', 'n_pagos', 'fuente', 'segmento', 'P_tipico',
           'd_tipico', 't', 'C', 'R', 'score', 'dias_liquidar', 'semaforo']


class ReportTests(unittest.TestCase):
    def test_direct_report_preserves_values_and_escapes_data(self):
        name = '</script><img src=x onerror=alert(1)> & José'
        frame = pd.DataFrame([[name, 100, 4, 'propio', 0, 30, 7.5, 10,
                               100/30, 10/7.5, 40/9, 100/3, 'amarillo']], columns=COLUMNS)
        with tempfile.TemporaryDirectory() as folder:
            path = generar_html(frame, Path(folder)/'report.html', date(2026, 10, 5))
            html = path.read_text(encoding='utf-8')
            report = json.loads(re.search(r'const REPORT = (.*);', html)[1])
            self.assertEqual(report['rows'][0]['c'], name)
            self.assertAlmostEqual(report['rows'][0]['dias'], 100/3, places=12)
            self.assertIn('Corte al 05/10/2026', html)
            self.assertNotIn(name, html)
            self.assertNotIn('__REPORT_', html)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_empty_and_missing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'empty.html'
            generar_html(pd.DataFrame(columns=COLUMNS), path)
            self.assertIn('"rows": []', path.read_text())
            frame = pd.DataFrame([['TEST', 100, 0, 'segmento', 0,
                                  None, None, None, None, 1, None, None, 'sin_datos']],
                                 columns=COLUMNS)
            generar_html(frame, path)
            self.assertIn('"dias": null', path.read_text())
            self.assertEqual(semaforo(float('nan')), 'sin_datos')


if __name__ == '__main__':
    unittest.main()
