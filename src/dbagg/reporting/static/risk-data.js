// Pure presentation rules shared by the offline report and regression tests.
function knownBalance(row) {
  return Number.isFinite(row.D);
}

function liquidationBase(row) {
  if (!knownBalance(row)) return null;
  if (row.D <= 0) return 0;
  return Number.isFinite(row.C) && Number.isFinite(row.d)
    ? row.C * Math.max(row.d, 1) : null;
}

function riskState(row, yellow, red) {
  if (!knownBalance(row)) return 'pendiente';
  if (row.D <= 0) return 'sin';
  if (row.motivo || !Number.isFinite(row.dias)) return 'pendiente';
  return row.dias > red ? 'rojo' : row.dias > yellow ? 'amarillo' : 'verde';
}

function rowInScope(row, source, minimum) {
  if (source !== 'todos' && row.f !== source) return false;
  // Unknown balances cannot be compared to a threshold; keep them visible.
  if (!knownBalance(row)) return true;
  return row.D <= 0 ? minimum === 0 : row.D > minimum;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {knownBalance, liquidationBase, riskState, rowInScope};
}
