"""Confirmed supplier contracts. Model arguments are values, never SQL fragments."""

import json

from dbagg.business.evidence import BusinessError

SUPPLIERS = "proadel.catalogo_proveedores_data_v"
SUPPLIER_PAYMENTS = "proadel.pago_proveedor_data"
PURCHASES = "proadel.compras_data"
SUPPLIER_PROTECTED_SOURCES = frozenset({SUPPLIERS, SUPPLIER_PAYMENTS})


def validate_supplier_context(context):
    """Local guidance cannot silently change the fixed, reviewed query contracts."""
    topics = json.loads(context).get("topics", {})
    expected = {
        "supplier_balance": (
            SUPPLIERS,
            {
                "supplier_key": "CODIGO",
                "supplier_name": "NOMBRE",
                "current_balance": "SALDO ACTUAL",
            },
        ),
        "supplier_payments": (
            SUPPLIER_PAYMENTS,
            {
                "supplier_key": "PROVEEDOR",
                "amount": "IMPORTE",
                "date": "FECHA",
                "status": "ESTADO",
                "record_id": "ID",
                "receipt": "FOLIO",
            },
        ),
    }
    for topic, (source, columns) in expected.items():
        definition = topics.get(topic, {})
        actual = definition.get("columns", {})
        if (
            any(actual.get(key) != value for key, value in columns.items())
            or definition.get("status") != "confirmed_by_owner"
            or [s.lower() for s in definition.get("preferred_sources", [])] != [source]
        ):
            raise BusinessError(
                "El contexto local difiere del contrato de consulta verificado; requiere revisión."
            )
    if topics["supplier_payments"].get("confirmed_filters") != {"ESTADO": "ACTIVA"}:
        raise BusinessError(
            "El filtro local de pagos difiere del contrato de consulta verificado; requiere revisión."
        )
