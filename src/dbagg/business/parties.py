"""Looks a name or code up in both catalogues; the same value can be two different parties."""

from dataclasses import dataclass

from dbagg.business.customers import CUSTOMERS, like_literal
from dbagg.business.evidence import label, records
from dbagg.business.suppliers import SUPPLIERS

CUSTOMER = "cliente"
SUPPLIER = "proveedor"
ROW_LIMIT = 5
AMBIGUOUS_MARK = "como cliente y como proveedor:"


@dataclass(frozen=True)
class PartyMatch:
    entity: str
    code: str
    name: str
    inactive: bool = False


class PartyDirectory:
    def __init__(self, db, allowed_tables):
        self.db = db
        self.allowed = set(allowed_tables)

    @property
    def supports_suppliers(self):
        return SUPPLIERS in self.allowed

    def _search(self, source, columns, term, exact_code):
        if exact_code:
            where, params = "[CODIGO] = ?", (term,)
        else:
            pattern = like_literal(term)
            where = "[CODIGO] LIKE ? ESCAPE '!' OR [NOMBRE] LIKE ? ESCAPE '!'"
            params = (pattern, pattern)
        result = self.db.query(
            f"SELECT TOP {ROW_LIMIT + 1} {columns} FROM {source} WHERE {where} "
            "ORDER BY CASE WHEN [CODIGO] = ? OR [NOMBRE] = ? THEN 0 ELSE 1 END, [CODIGO]",
            params=params + (term, term),
            row_limit=ROW_LIMIT + 1,
        )
        return records(result)

    def find(self, term, exact_code=False):
        """Up to one row more than a list shows, so a caller can tell that more exist."""
        found = {CUSTOMER: [], SUPPLIER: []}
        if CUSTOMERS in self.allowed:
            rows = self._search(
                CUSTOMERS, "[CODIGO] AS codigo, [NOMBRE] AS nombre", term, exact_code
            )
            found[CUSTOMER] = [PartyMatch(CUSTOMER, r["codigo"], r["nombre"]) for r in rows]
        if self.supports_suppliers:
            rows = self._search(
                SUPPLIERS,
                "[CODIGO] AS codigo, [NOMBRE] AS nombre, [ESTADO] AS estado",
                term,
                exact_code,
            )
            found[SUPPLIER] = [
                PartyMatch(
                    SUPPLIER,
                    r["codigo"],
                    r["nombre"],
                    str(r.get("estado") or "").strip().upper() == "INACTIVO",
                )
                for r in rows
            ]
        return found


def ambiguous_answer(term, matches):
    lines = [f"Encontré «{label(term)}» {AMBIGUOUS_MARK}"]
    more = False
    for entity in (CUSTOMER, SUPPLIER):
        rows = matches[entity]
        more |= len(rows) > ROW_LIMIT
        for match in rows[:ROW_LIMIT]:
            suffix = " · inactivo" if match.inactive else ""
            lines.append(f"• {label(match.name)} ({entity}, código {label(match.code)}){suffix}")
    if more:
        lines.append("Hay más coincidencias; precisa el nombre o código.")
    lines.append("¿Cuál quieres consultar?")
    return "\n".join(lines)
