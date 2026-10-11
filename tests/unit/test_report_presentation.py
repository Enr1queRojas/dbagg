import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(
    shutil.which("node"), "Node.js is required to execute report presentation rules"
)
class ReportPresentationTests(unittest.TestCase):
    def test_unknown_balance_is_visible_and_never_classified_as_no_debt(self):
        root = Path(__file__).resolve().parents[2]
        script = r"""
const assert = require('node:assert/strict');
const {riskState, liquidationBase, rowInScope} = require('./src/dbagg/reporting/static/risk-data.js');
const unknown = {D: null, C: null, d: null, dias: null, f: 'segmento'};
assert.equal(riskState(unknown, 45, 90), 'pendiente');
assert.equal(liquidationBase(unknown), null);
assert.equal(rowInScope(unknown, 'todos', 1000), true);
assert.equal(riskState({D: 0}, 45, 90), 'sin');
assert.equal(riskState({D: -10}, 45, 90), 'sin');
assert.equal(riskState({D: 100, dias: 10}, 45, 90), 'verde');
assert.equal(riskState({D: 100, dias: 100}, 45, 90), 'rojo');
assert.equal(riskState({D: 100, dias: 10, motivo: 'Fecha inválida'}, 45, 90), 'pendiente');
assert.equal(rowInScope({D: 0}, 'todos', 1000), false);
assert.equal(rowInScope({D: 0}, 'todos', 0), true);
"""
        subprocess.run(
            [shutil.which("node"), "-e", script], cwd=root, check=True, capture_output=True
        )


if __name__ == "__main__":
    unittest.main()
