"""Local allowlist, constrained by the existing SQL catalogue and runtime grants."""

from dbagg.business.customers import CREDITS, CUSTOMERS, PAYMENTS, OPERATIONS
from dbagg.business.suppliers import (
    PURCHASES,
    SUPPLIER_OPERATIONS,
    SUPPLIER_PAYMENTS,
    SUPPLIERS,
)
from dbagg.routing.contracts import Route

CUSTOMER_TOPICS = (
    "customer_balance",
    "customer_statement",
    "collections",
    "supplier_balance",
    "supplier_payments",
)
PARTY_MODULES = ("shared/identity", "shared/time")
DESCRIPTIONS = {
    "saldo": "Usar cuando piden el saldo o la deuda actual de una persona nombrada o ya "
    "consultada. No usar cuando: movimientos, saldo a una fecha pasada o ranking.",
    "saldos_coincidencias": "Usar cuando piden el saldo de cada persona con un mismo nombre. "
    "No usar cuando: sumar o comparar saldos de un grupo.",
    "contar_coincidencias": "Usar cuando preguntan cuántas personas coinciden con un nombre. "
    "No usar cuando: conteo global sin nombre.",
    "ranking_deuda": "Usar cuando piden explícitamente quiénes deben más o un top de deuda. "
    "No usar cuando: nombran a una persona.",
    "pagos": "Usar cuando piden la lista de pagos o abonos de una persona. "
    "No usar cuando: piden cuánto pagó en total.",
    "ultimo_pago": "Usar cuando piden la fecha o el importe del último pago de una persona. "
    "No usar cuando: no dicen «último» o «reciente»; tampoco para notas o compras.",
    "total_pagos": "Usar cuando preguntan cuánto pagó una persona, también «hoy» o en un "
    "período. No usar cuando: gastos («cuánto gastamos», «total de gastos registrados»).",
    "notas_pendientes": "Usar cuando piden las notas abiertas o de qué se compone el saldo "
    "actual. No usar cuando: la última nota.",
    "estado_cuenta": "Usar cuando piden el estado de cuenta o reporte completo de una persona. "
    "No usar cuando: solo pagos o solo saldo.",
    "ultima_nota": "Usar cuando piden la última nota, última compra o compra a crédito de una "
    "persona. No usar cuando: piden pagos; una nota no es un pago.",
}


SUPPLIER_DESCRIPTIONS = {
    "saldo": "Usar cuando piden cuánto le debemos a un proveedor o su saldo («le debemos», "
    "«proveedor»). No usar cuando: «nos debe» o un cliente.",
    "ranking_deuda": "Usar cuando piden a qué proveedores les debemos más. No usar cuando: "
    "nombran a uno.",
    "pagos": "Usar cuando piden la lista de pagos que le hicimos a un proveedor. No usar cuando: "
    "cuánto le pagamos en total.",
    "total_pagos": "Usar cuando preguntan cuánto le pagamos a un proveedor, en total o en un "
    "período. No usar cuando: pagos de un cliente.",
    "ultimo_pago": "Usar cuando piden el último pago que le hicimos a un proveedor. No usar "
    "cuando: notas o compras.",
    "ultima_nota": "Usar cuando piden la última nota o compra a un proveedor («lo último que le "
    "compramos»). No usar cuando: pagos.",
    "movimientos": "Usar cuando piden los movimientos (compras y pagos) de un proveedor, con o sin "
    "período. No usar cuando: un cliente o gastos.",
    "notas_pendientes": "Usar cuando piden qué notas le debemos a un proveedor. No usar cuando: "
    "la última nota.",
    "estado_cuenta": "Usar cuando piden el estado de cuenta de un proveedor. No usar cuando: solo "
    "pagos o solo saldo.",
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
            if operation == "ultima_nota" and not {CREDITS, PURCHASES} & tables:
                continue
            name = "clientes." + operation
            operation_topics = ("customer_balance", "supplier_balance")
            operation_sources = frozenset({CUSTOMERS, SUPPLIERS}) & sources
            if operation in {"pagos", "total_pagos", "ultimo_pago"}:
                operation_topics += ("collections", "supplier_payments")
                operation_sources |= frozenset({PAYMENTS, SUPPLIER_PAYMENTS}) & sources
            elif operation == "ultima_nota":
                # Customers' notes live in credits; suppliers' in purchases.
                operation_sources |= frozenset({CREDITS, PURCHASES}) & tables
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
                modules=PARTY_MODULES,
            )
    if "proveedores" in domains and SUPPLIERS in tables:
        # Same party prompts; both catalogues stay available so homonyms are still asked.
        identity = frozenset({CUSTOMERS, SUPPLIERS}) & tables
        for operation in (*SUPPLIER_OPERATIONS, "estado_cuenta"):
            needs = {
                "pagos": {SUPPLIER_PAYMENTS},
                "total_pagos": {SUPPLIER_PAYMENTS},
                "ultimo_pago": {SUPPLIER_PAYMENTS},
                "ultima_nota": {PURCHASES},
                "movimientos": {SUPPLIER_PAYMENTS, PURCHASES},
                "notas_pendientes": {SUPPLIER_PAYMENTS, PURCHASES},
                "estado_cuenta": {SUPPLIER_PAYMENTS, PURCHASES},
            }.get(operation, set())
            if not needs <= tables:
                continue
            name = "proveedores." + operation
            routes[name] = Route(
                name,
                "proveedores",
                operation,
                SUPPLIER_DESCRIPTIONS.get(operation, DESCRIPTIONS.get(operation)),
                ("customer_balance", "supplier_balance", "supplier_payments"),
                identity | frozenset(needs),
                ("consultar_negocio", "pedir_aclaracion"),
                "business",
                modules=PARTY_MODULES,
            )
    expenses = frozenset(t for t in tables if catalog.sources[t].domain == "gastos")
    if "gastos" in domains and expenses:
        name = "gastos.exploracion"
        routes[name] = Route(
            name,
            "gastos",
            None,
            "Usar cuando preguntan por gastos, destinos o apartados de gasto: saldos, "
            "movimientos o totales («cuánto gastamos», «total de gastos registrados»), aunque "
            "antes se hablara de pagos de una persona. No usar cuando: clientes o proveedores.",
            (),
            expenses,
            ("describir_tablas", "consultar_sql", "pedir_aclaracion"),
            "exploration",
            modules=("shared/time",),
        )
    return routes


def route_options(routes):
    return {
        **{name: route.description for name, route in routes.items()},
        "legacy": "Usar cuando: cuentas de banco o efectivo de personas (no son clientes ni "
        "proveedores), inventario, proveedores explícitos sin una opción propia, varios conceptos, ruta dudosa, fuera "
        "del alcance de estas opciones, o una respuesta corta («sí», un código o una posición de "
        "una lista) que continúa una operación pendiente. Usa el intérprete completo.",
    }


def domain_routes(routes, domain, names=None):
    """Routes of one domain, optionally limited to the given names, in registry order."""
    return [
        route
        for name, route in routes.items()
        if route.domain == domain and (names is None or name in names)
    ]
