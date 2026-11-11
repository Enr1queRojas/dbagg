"""Local allowlist, constrained by the existing SQL catalogue and runtime grants."""

from dbagg.business.customers import CUSTOMERS, PAYMENTS, OPERATIONS
from dbagg.business.suppliers import SUPPLIERS, SUPPLIER_PAYMENTS
from dbagg.routing.contracts import Route

CUSTOMER_TOPICS = (
    "customer_balance",
    "customer_statement",
    "collections",
    "supplier_balance",
    "supplier_payments",
)
DESCRIPTIONS = {
    "saldo": "Saldo actual de una persona; no movimientos ni saldo histórico.",
    "saldos_coincidencias": "Saldos separados de todos los homónimos; nunca sumar.",
    "contar_coincidencias": "Contar coincidencias de un nombre; no conteo global.",
    "ranking_deuda": "Ranking global de deuda actual pedido explícitamente.",
    "pagos": "Lista de pagos de una persona, sin representar el total.",
    "ultimo_pago": "Pagos de la última fecha de una persona, sin período.",
    "total_pagos": "Cuánto ha pagado una persona, total del período o de todo.",
    "notas_pendientes": "Notas abiertas que componen el saldo actual.",
    "estado_cuenta": "Estado de cuenta o reporte completo de una persona.",
}


def available_routes(catalog, tables, domains=("clientes", "gastos")):
    tables = catalog.restrict(tables)
    routes = {}
    if "clientes" in domains and CUSTOMERS in tables:
        # Keep both party catalogues available: a shared name must still be disambiguated.
        sources = frozenset(
            t
            for t in tables
            if catalog.sources[t].domain == "clientes" or t in {SUPPLIERS, SUPPLIER_PAYMENTS}
        )
        for operation in (*OPERATIONS, "estado_cuenta"):
            if (
                operation in {"pagos", "total_pagos", "ultimo_pago", "estado_cuenta"}
                and PAYMENTS not in tables
            ):
                continue
            name = "clientes." + operation
            operation_topics = ("customer_balance", "supplier_balance")
            operation_sources = frozenset({CUSTOMERS, SUPPLIERS}) & sources
            if operation in {"pagos", "total_pagos", "ultimo_pago"}:
                operation_topics += ("collections", "supplier_payments")
                operation_sources |= frozenset({PAYMENTS, SUPPLIER_PAYMENTS}) & sources
            elif operation in {"notas_pendientes", "estado_cuenta"}:
                operation_topics = CUSTOMER_TOPICS
                operation_sources = sources
            routes[name] = Route(
                name,
                "clientes",
                operation,
                DESCRIPTIONS[operation],
                operation_topics,
                operation_sources,
                ("consultar_negocio", "pedir_aclaracion"),
                "business",
            )
    expenses = frozenset(t for t in tables if catalog.sources[t].domain == "gastos")
    if "gastos" in domains and expenses:
        name = "gastos.exploracion"
        routes[name] = Route(
            name,
            "gastos",
            None,
            "Saldos, movimientos o totales de gastos y destinos; no clientes.",
            (),
            expenses,
            ("describir_tablas", "consultar_sql", "pedir_aclaracion"),
            "exploration",
        )
    return routes


def route_options(routes):
    return {
        **{name: route.description for name, route in routes.items()},
        "legacy": "Bancos, inventario, proveedores explícitos, varios conceptos, ruta dudosa o fuera del alcance de estas opciones. Usar el intérprete completo.",
    }
