"""Versioned catalogue of SQL sources the agent may read: domain, status and columns.

The catalogue is the ceiling for SQL_ALLOWED_TABLES: the environment can narrow it,
never extend it. Adding a source requires changing this package file and its tests.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

STATUSES = frozenset({"verified", "candidate"})
ACCESS = frozenset({"business_tool", "exploratory"})
_OBJECT = re.compile(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*")


@dataclass(frozen=True)
class Source:
    name: str
    display: str
    domain: str
    status: str
    access: str
    description: str
    columns: frozenset


@dataclass(frozen=True)
class Catalog:
    sources: dict
    domains: dict
    joins: frozenset
    denied: frozenset
    version: str

    def restrict(self, tables):
        """Objects that are both configured and catalogued."""
        return frozenset(t.lower() for t in tables if t.lower() in self.sources)

    def allowed_columns(self, tables):
        return frozenset().union(*(self.sources[t].columns for t in tables if t in self.sources))

    def join_allowed(self, tables):
        tables = frozenset(tables)
        return len(tables) <= 1 or (len(tables) == 2 and tables in self.joins)

    def filter_schema(self, described):
        """Drop columns outside the catalogue from a describir_tablas result."""
        filtered = []
        for item in described:
            source = self.sources.get(str(item.get("table", "")).lower())
            if source is None:
                continue
            columns = [
                c
                for c in item.get("columns", [])
                if str(c.get("name", "")).lower() in source.columns
            ]
            filtered.append(dict(item, columns=columns))
        return filtered

    def cards(self, tables):
        """Compact description of the authorised objects for the model."""
        selected = [self.sources[t] for t in sorted(self.restrict(tables))]
        return {
            "dominios": {
                d: text for d, text in self.domains.items() if any(s.domain == d for s in selected)
            },
            "objetos": [
                {
                    "objeto": s.display,
                    "dominio": s.domain,
                    "estado": s.status,
                    "acceso": s.access,
                    "descripcion": s.description,
                }
                for s in selected
            ],
        }


def parse_catalog(text):
    data = json.loads(text)
    domains = data.get("domains")
    denied = frozenset(c.lower() for c in data.get("denied_columns", []))
    if not isinstance(domains, dict) or not domains:
        raise ValueError("Catálogo SQL sin dominios.")
    sources = {}
    for raw in data.get("sources", []):
        display = raw.get("object", "")
        name = display.lower()
        columns = frozenset(c.lower() for c in raw.get("columns", []))
        if (
            not _OBJECT.fullmatch(name)
            or name in sources
            or raw.get("domain") not in domains
            or raw.get("status") not in STATUSES
            or raw.get("access") not in ACCESS
            or not str(raw.get("description", "")).strip()
            or not columns
            or columns & denied
        ):
            raise ValueError(f"Fuente inválida en el catálogo SQL: {display!r}.")
        sources[name] = Source(
            name, display, raw["domain"], raw["status"], raw["access"], raw["description"], columns
        )
    joins = set()
    for pair in data.get("joins", []):
        pair = frozenset(t.lower() for t in pair)
        if len(pair) != 2 or not pair <= set(sources):
            raise ValueError("Relación inválida en el catálogo SQL.")
        joins.add(pair)
    if not sources:
        raise ValueError("Catálogo SQL vacío.")
    fingerprint = hashlib.sha256(text.encode()).hexdigest()[:16]
    version = f"catalog-v{data.get('version')}:{fingerprint}"
    return Catalog(sources, dict(domains), frozenset(joins), denied, version)


@lru_cache(maxsize=1)
def load_catalog():
    text = resources.files("dbagg.database").joinpath("catalog.json").read_text(encoding="utf-8")
    return parse_catalog(text)
