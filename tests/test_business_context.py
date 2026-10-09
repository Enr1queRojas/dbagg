import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business_context import load_business_context


class BusinessContextTests(unittest.TestCase):
    def test_defaults_are_active_without_local_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Path(__file__).resolve().parents[1] / 'business_context.default.json'
            (root / source.name).write_bytes(source.read_bytes())
            data = json.loads(load_business_context(root))
        self.assertIn('topics', data)
        self.assertIn('sales', data['topics'])
        self.assertIn('proadel.CATALOGO_CLIENTES_DATA_V',
                      data['topics']['customer_balance']['preferred_sources'])

    def test_local_nested_changes_keep_unrelated_defaults_and_original_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            default = root / 'business_context.default.json'
            local = root / 'business_context.json'
            base = {'topics': {'sales': {'pending': ['IVA', 'fecha'], 'columns': {'amount': None}},
                              'inventory': {'status': 'pending'}}}
            override = {'topics': {'sales': {'pending': [], 'columns': {'amount': 'IMPORTE_REAL'}}}}
            default.write_text(json.dumps(base), encoding='utf-8')
            local.write_text(json.dumps(override), encoding='utf-8-sig')
            merged = json.loads(load_business_context(root))
            self.assertEqual(merged['topics']['sales']['pending'], [])
            self.assertEqual(merged['topics']['sales']['columns']['amount'], 'IMPORTE_REAL')
            self.assertEqual(merged['topics']['inventory'], {'status': 'pending'})
            self.assertEqual(json.loads(default.read_text()), base)
            self.assertEqual(json.loads(local.read_text(encoding='utf-8-sig')), override)

    def test_invalid_local_data_does_not_fall_back_silently(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / 'business_context.json'
            for content in ('{invalido', '[]', '{"notes":"' + 'x' * 16000 + '"}'):
                with self.subTest(content_length=len(content)):
                    local.write_text(content, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_business_context(root)

    def test_legacy_local_definitions_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = {'definitions': {'saldo': 'definición propia'}, 'relationships': []}
            (root / 'business_context.json').write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(json.loads(load_business_context(root)), data)

    def test_previously_valid_local_context_remains_valid_with_shipped_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Path(__file__).resolve().parents[1] / 'business_context.default.json'
            (root / source.name).write_bytes(source.read_bytes())
            local = {'definitions': {'notes': 'x' * 15000}}
            (root / 'business_context.json').write_text(json.dumps(local), encoding='utf-8')
            merged = json.loads(load_business_context(root))
            self.assertEqual(merged['definitions'], local['definitions'])
            self.assertIn('customer_balance', merged['topics'])


if __name__ == '__main__':
    unittest.main()
