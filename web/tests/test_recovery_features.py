import copy
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.privacy import PrivacyStore
from backend.store import Store, StoreError


class RecoveryFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'ledger.sqlite3')

    def fixture(self):
        parent = self.store.create('assets', {'name': 'root', 'kind': 'folder'})
        asset = self.store.create('assets', {'name': 'leaf', 'kind': 'folder', 'parent_id': parent['id'], 'backup_target': True})
        version = self.store.create('versions', {'asset_id': asset['id'], 'label': 'history'})
        location = self.store.create('locations', {'name': 'disk', 'kind': 'other'})
        record = self.store.create('records', {'version_id': version['id'], 'location_id': location['id'], 'backed_up_at': '2026-10-01T00:00:00Z', 'notes': 'original'})
        return parent, asset, version, location, record

    def test_bulk_paths_reuse_and_atomic_conflict(self):
        result = self.store.bulk_assets(None, 'folder', ['Photos/2025', 'Photos/2026', 'Photos/2025'])
        self.assertEqual(result, {'created': 3, 'skipped': 1})
        before = self.store.state()
        with self.assertRaises(StoreError):
            self.store.bulk_assets(None, 'file', ['new/file.txt', 'Photos'])
        self.assertEqual(self.store.state(), before)

    def test_deep_bulk_path_rejects_before_prefix_expansion(self):
        from backend.recovery import path_parts
        before = self.store.state()
        with self.assertRaises(StoreError) as error:
            path_parts(['/'.join(['a'] * 4096)])
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(len(path_parts(['/'.join(['a'] * 500)])[0]), 500)
        for path in ('/a', '../a', 'a/../b', 'a//b', 'a\\b', 'C:/a', 'a/', './a', 'a/\x00'):
            with self.subTest(path=path), self.assertRaises(StoreError):
                self.store.bulk_assets(None, 'folder', [path])
        self.assertEqual(self.store.state(), before)

    def test_bulk_record_edit_selected_fields_and_atomic_missing(self):
        _, _, _, _, record = self.fixture()
        result = self.store.bulk_records([record['id']], {'location_name': 'new disk', 'notes': ''})
        self.assertEqual(result, {'updated': 1})
        current = self.store.state()['records'][0]
        self.assertEqual(current['backed_up_at'], record['backed_up_at'])
        self.assertEqual(current['notes'], '')
        self.assertNotEqual(current['location_id'], record['location_id'])
        before = self.store.state()
        with self.assertRaises(StoreError):
            self.store.bulk_records([record['id'], str(uuid.uuid4())], {'location_name': 'orphan'})
        self.assertEqual(self.store.state(), before)
        for changes in ({}, {'id': record['id']}, {'backed_up_at': 'invalid'}, {'notes': 'x' * 2001}):
            with self.assertRaises(StoreError):
                self.store.bulk_records([record['id']], changes)

    def test_trash_retains_ids_and_dependencies_and_prevents_overwrite(self):
        _, asset, version, location, record = self.fixture()
        trash = self.store.delete('records', record['id'])['trash_id']
        self.assertEqual(self.store.trash()[0]['name'], 'root/leaf')
        self.store.restore_trash(trash)
        self.assertEqual(self.store.state()['records'][0], record)
        trash = self.store.delete('records', record['id'])['trash_id']
        removed = self.store.delete('assets', asset['id'])['trash_id']
        with self.assertRaises(StoreError):
            self.store.restore_trash(trash)
        self.store.restore_trash(removed)
        self.assertIn(version, self.store.state()['versions'])
        self.store.restore_trash(trash)
        self.assertIn(record, self.store.state()['records'])
        item = self.store.create('assets', {'name': 'single', 'kind': 'file'})
        deleted = self.store.delete('assets', item['id'])['trash_id']
        self.store.create('assets', {'name': 'single', 'kind': 'file'})
        with self.assertRaises(StoreError):
            self.store.restore_trash(deleted)
        self.assertIn(deleted, [entry['id'] for entry in self.store.trash()])

    def test_trash_expiry_and_privacy_follow_current_ancestors(self):
        parent, asset, _, _, record = self.fixture()
        deleted = self.store.delete('records', record['id'])['trash_id']
        self.store.set_private(parent['id'], True)
        self.assertEqual(PrivacyStore(self.store, False).trash(), [])
        with self.assertRaises(StoreError) as denied:
            PrivacyStore(self.store, False).restore_trash(deleted)
        self.assertEqual(denied.exception.status, 404)
        self.assertEqual(len(PrivacyStore(self.store, True).trash()), 1)
        future = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat().replace('+00:00', 'Z')
        with patch('backend.recovery.utc_now', return_value=future):
            self.assertEqual(self.store.trash(), [])
            with self.assertRaises(StoreError):
                self.store.restore_trash(deleted)

    def test_bulk_never_accesses_hidden_existing_path_or_record(self):
        parent, asset, _, _, record = self.fixture()
        self.store.set_private(asset['id'], True)
        before = self.store.state()
        for call in (lambda: PrivacyStore(self.store, False).bulk_assets(parent['id'], 'folder', ['leaf/new']),
                     lambda: PrivacyStore(self.store, False).bulk_records([record['id']], {'notes': 'leak'})):
            with self.assertRaises(StoreError):
                call()
        self.assertEqual(self.store.state(), before)

    def test_full_restore_replaces_and_rolls_back_invalid_graph(self):
        _, asset, _, _, _ = self.fixture()
        snapshot = self.store.export()
        self.store.update('assets', asset['id'], {'name': 'changed'})
        self.store.restore_state(snapshot)
        self.assertEqual(self.store.state(), {key: snapshot[key] for key in ('assets', 'versions', 'locations', 'records')})
        invalid = copy.deepcopy(snapshot)
        invalid['records'][0]['version_id'] = str(uuid.uuid4())
        before = self.store.state()
        with self.assertRaises(StoreError):
            self.store.restore_state(invalid)
        self.assertEqual(self.store.state(), before)
