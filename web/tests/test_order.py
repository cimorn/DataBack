import copy
import tempfile
import unittest
from pathlib import Path
from backend.store import Store, StoreError

class OrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'order.sqlite3'
        self.store = Store(self.db)
    def asset(self, name, parent_id=None, kind='file'):
        return self.store.create('assets', {'name': name, 'kind': kind, 'parent_id': parent_id})
    def test_reorder_persists_mixed_siblings_and_fact_fields_across_restart_import(self):
        a = self.asset('A'); b = self.asset('B', kind='folder'); child = self.asset('child', b['id'])
        before = self.store.state()
        self.store.reorder_assets(None, [b['id'], a['id']])
        positions = {x['id']: x.get('manual_order') for x in self.store.state()['assets']}
        self.assertEqual(positions, {a['id']: 1, b['id']: 0, child['id']: None})
        for original in before['assets']:
            updated = next(x for x in self.store.state()['assets'] if x['id'] == original['id'])
            self.assertEqual({k: v for k, v in updated.items() if k != 'manual_order'}, original)
        self.assertEqual(Store(self.db).state(), self.store.state())
        exported = self.store.export(); restored = Store(Path(self.temp.name) / 'import.sqlite3')
        restored.import_data(exported)
        self.assertEqual(restored.state(), self.store.state())
        edited = self.store.update('assets', a['id'], {'notes': 'changed'})
        self.assertEqual(edited['manual_order'], 1)
        self.assertEqual(self.asset('appended')['manual_order'], 2)
    def test_invalid_reorders_are_atomic(self):
        a = self.asset('A'); b = self.asset('B', kind='folder'); child = self.asset('child', b['id'])
        self.store.reorder_assets(None, [a['id'], b['id']]); before = self.store.state()
        for parent, ids in [(None, [a['id']]), (None, [a['id'], a['id']]), (None, [a['id'], child['id']]), (a['id'], []), (None, 'wrong')]:
            with self.subTest(parent=parent, ids=ids), self.assertRaises(StoreError): self.store.reorder_assets(parent, ids)
            self.assertEqual(self.store.state(), before)
    def test_parent_move_appends_and_does_not_reuse_old_directory_position(self):
        root = self.asset('root', kind='folder'); a = self.asset('A'); b = self.asset('B')
        child = self.asset('child', root['id'])
        self.store.reorder_assets(None, [b['id'], root['id'], a['id']])
        self.store.reorder_assets(root['id'], [child['id']])
        moved = self.store.update('assets', b['id'], {'parent_id': root['id'], 'notes': 'moved'})
        self.assertEqual(moved['manual_order'], 1)
        self.assertEqual(moved['created_at'], b['created_at'])
        self.assertEqual(self.store.update('assets', a['id'], {'notes': 'kept'})['manual_order'], 2)

    def test_invalid_parent_edit_is_validation_error_before_append_query(self):
        a = self.asset('A'); before = self.store.state()
        with self.assertRaises(StoreError) as caught:
            self.store.update('assets', a['id'], {'parent_id': {'$invalid': True}})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.store.state(), before)

    def test_optional_legacy_field_and_bounded_values(self):
        a = self.asset('legacy'); snapshot = self.store.export()
        for entry in snapshot['assets']: entry.pop('manual_order', None)
        restored = Store(Path(self.temp.name) / 'old.sqlite3'); restored.import_data(snapshot)
        self.assertEqual(restored.state()['assets'][0]['id'], a['id'])
        for value in [-1, True, 1.5, '1', 2147483648]:
            broken = copy.deepcopy(snapshot); broken['assets'][0]['manual_order'] = value
            with self.subTest(value=value), self.assertRaises(StoreError): restored.import_data(broken)
        self.assertEqual(self.store.update('assets', a['id'], {'manual_order': 2147483647})['manual_order'], 2147483647)

if __name__ == '__main__': unittest.main()
