"""Synthetic supplier scenarios; SQLite exercises queries, not SQL Server compatibility."""

import json
import unittest

from dbagg.business.evidence import BusinessError
from dbagg.business.suppliers import (
    PURCHASES,
    SUPPLIER_PAYMENTS,
    SUPPLIERS,
    validate_supplier_context,
)
from dbagg.context.loader import load_business_context
from dbagg.database.catalog import load_catalog


class SupplierContractTests(unittest.TestCase):
    def test_supplier_sources_are_business_tool(self):
        catalog = load_catalog()
        for source in (SUPPLIERS, SUPPLIER_PAYMENTS):
            self.assertEqual(catalog.sources[source].access, "business_tool")

    def test_purchases_stay_exploratory(self):
        self.assertEqual(load_catalog().sources[PURCHASES].access, "exploratory")

    def test_default_context_matches_supplier_contract(self):
        validate_supplier_context(load_business_context())

    def test_changed_supplier_column_is_rejected(self):
        context = json.loads(load_business_context())
        context["topics"]["supplier_balance"]["columns"]["current_balance"] = "IMPORTE"
        with self.assertRaisesRegex(BusinessError, "contrato"):
            validate_supplier_context(json.dumps(context))

    def test_changed_payment_filter_is_rejected(self):
        context = json.loads(load_business_context())
        context["topics"]["supplier_payments"]["confirmed_filters"] = {}
        with self.assertRaisesRegex(BusinessError, "contrato"):
            validate_supplier_context(json.dumps(context))


if __name__ == "__main__":
    unittest.main()
