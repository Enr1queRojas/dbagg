"""Which party a question means: wording, conversation and both catalogues."""

import unittest

from dbagg.agent.entity import (
    conversation_entity,
    entity_in_answer,
    explicit_entity,
    listed_row,
)
from dbagg.agent.intent import listed_codes
from dbagg.business.customers import CUSTOMERS
from dbagg.business.parties import (
    CUSTOMER,
    SUPPLIER,
    PartyDirectory,
    PartyMatch,
    ambiguous_answer,
)
from dbagg.business.suppliers import SUPPLIERS
from tests.unit.test_supplier_queries import SupplierDatabase


def assistant(content):
    return {"role": "assistant", "content": content}


class ExplicitEntityTests(unittest.TestCase):
    def test_explicit_supplier_phrases(self):
        for question in (
            "¿cuánto le debemos a Cuervo?",
            "saldo del proveedor Cuervo",
            "le compramos a Cuervo",
            "¿A quién le debemos más?",
            "¿Cuánto le hemos pagado este mes?",
        ):
            with self.subTest(question=question):
                self.assertEqual(explicit_entity(question), SUPPLIER)

    def test_explicit_customer_phrases(self):
        for question in (
            "¿cuánto nos debe Cuervo?",
            "saldo del cliente Cuervo",
            "¿Quiénes nos deben más?",
        ):
            with self.subTest(question=question):
                self.assertEqual(explicit_entity(question), CUSTOMER)

    def test_bare_debe_decides_nothing(self):
        self.assertIsNone(explicit_entity("¿Cuánto debe Cuervo?"))

    def test_a_name_starting_like_supplier_decides_nothing(self):
        self.assertIsNone(explicit_entity("saldo de Proveedora del Norte"))

    def test_both_words_decide_nothing(self):
        self.assertIsNone(explicit_entity("¿Ana es cliente o proveedor?"))


class ConversationEntityTests(unittest.TestCase):
    def test_supplier_answer_is_recognized(self):
        self.assertEqual(entity_in_answer("Proveedor Acme (código ACME)\nLe debemos: 1"), SUPPLIER)

    def test_supplier_list_is_recognized(self):
        self.assertEqual(entity_in_answer("Encontré varios proveedores:\n• A (código 1)"), SUPPLIER)

    def test_customer_named_like_supplier_stays_customer(self):
        text = "Saldo actual de Proveedora del Norte (código PN): 1."
        self.assertEqual(entity_in_answer(text), CUSTOMER)

    def test_conversation_uses_latest_mention(self):
        history = [
            assistant("Saldo actual de Ana (código X): 1."),
            assistant("Proveedor Ana (código X)\nLe debemos: 1"),
        ]
        self.assertEqual(conversation_entity("X", history), SUPPLIER)

    def test_unmentioned_party_has_no_entity(self):
        self.assertIsNone(conversation_entity("Otro", [assistant("Proveedor Ana (código X)")]))

    def test_user_messages_do_not_set_the_entity(self):
        history = [{"role": "user", "content": "Proveedor Ana (código X)"}]
        self.assertIsNone(conversation_entity("X", history))

    def test_ambiguity_question_sets_no_entity(self):
        text = ambiguous_answer(
            "Ana",
            {
                CUSTOMER: [PartyMatch(CUSTOMER, "A1", "Ana")],
                SUPPLIER: [PartyMatch(SUPPLIER, "P1", "Ana")],
            },
        )
        self.assertIsNone(conversation_entity("Ana", [assistant(text)]))


class ListedRowTests(unittest.TestCase):
    MIXED = "• Ana (cliente, código A1)\n• Ana (proveedor, código P1)\n¿Cuál quieres consultar?"

    def test_listed_codes_reads_mixed_rows(self):
        self.assertEqual(listed_codes(self.MIXED), ["A1", "P1"])

    def test_inactive_rows_stay_listed(self):
        text = "• Ana (código P1) · inactivo\n• Luis (código P2)"
        self.assertEqual(listed_codes(text), ["P1", "P2"])

    def test_balance_rows_with_inactive_stay_listed(self):
        text = "1. Ana (código P1): le debemos 5.00 · inactivo\n2. Luis (código P2): sin saldo"
        self.assertEqual(listed_codes(text), ["P1", "P2"])

    def test_movements_never_match(self):
        self.assertEqual(listed_codes("• 2026-10-01 · compra · folio 7 · +1.00"), [])

    def test_row_of_a_mixed_list_keeps_its_entity(self):
        self.assertEqual(listed_row([assistant(self.MIXED)], 2), (SUPPLIER, "P1"))
        self.assertEqual(listed_row([assistant(self.MIXED)], 1), (CUSTOMER, "A1"))

    def test_row_of_a_supplier_list_is_a_supplier(self):
        text = "Encontré varios proveedores:\n• A (código 1)\n• B (código 2)"
        self.assertEqual(listed_row([assistant(text)], 2), (SUPPLIER, "2"))

    def test_row_of_a_customer_list_is_a_customer(self):
        text = "Encontré varios clientes:\n• A (código 1)\n• B (código 2)"
        self.assertEqual(listed_row([assistant(text)], 1), (CUSTOMER, "1"))

    def test_position_outside_the_list_is_none(self):
        self.assertIsNone(listed_row([assistant(self.MIXED)], 3))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.db = SupplierDatabase()
        self.addCleanup(self.db.conn.close)
        self.db.conn.execute(f'CREATE TABLE {CUSTOMERS} (CODIGO, NOMBRE, "SALDO ACTUAL")')
        self.db.conn.execute(f"INSERT INTO {CUSTOMERS} VALUES ('A1', 'Ana Cliente', 1)")
        self.db.supplier("P1", "Ana Proveedora", state="INACTIVO")
        self.directory = PartyDirectory(self.db, {CUSTOMERS, SUPPLIERS})

    def test_finds_both_sides(self):
        found = self.directory.find("Ana")
        self.assertEqual([m.code for m in found[CUSTOMER]], ["A1"])
        self.assertEqual([m.code for m in found[SUPPLIER]], ["P1"])
        self.assertTrue(found[SUPPLIER][0].inactive)

    def test_exact_code_does_not_match_names(self):
        self.assertEqual(self.directory.find("Ana", exact_code=True)[CUSTOMER], [])

    def test_wildcards_in_the_term_are_literal(self):
        found = self.directory.find("A%a")
        self.assertEqual(found[CUSTOMER], [])
        self.assertEqual(found[SUPPLIER], [])

    def test_without_supplier_sources_suppliers_are_not_queried(self):
        directory = PartyDirectory(self.db, {CUSTOMERS})
        self.assertFalse(directory.supports_suppliers)
        self.assertEqual(directory.find("Ana")[SUPPLIER], [])

    def test_ambiguous_answer_lists_both_entities(self):
        text = ambiguous_answer("Ana", self.directory.find("Ana"))
        self.assertTrue(text.startswith("Encontré «Ana» como cliente y como proveedor:"))
        self.assertIn("• Ana Cliente (cliente, código A1)", text)
        self.assertIn("• Ana Proveedora (proveedor, código P1) · inactivo", text)
        self.assertEqual(listed_codes(text), ["A1", "P1"])
