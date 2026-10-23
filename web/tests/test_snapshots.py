import copy
import gzip
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.store import Store, StoreError


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        from backend.snapshots import SnapshotManager
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'ledger.sqlite3')
        self.clock = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.manager = SnapshotManager(self.store, self.root / 'snapshots', threading.RLock(), now=lambda: self.clock)

    def test_daily_is_durable_and_once_per_day(self):
        from backend.snapshots import SnapshotManager
        self.manager.ensure_daily()
        self.manager.ensure_daily()
        self.assertEqual(len(self.manager.list()['items']), 1)
        restarted = SnapshotManager(self.store, self.root / 'snapshots', threading.RLock(), now=lambda: self.clock)
        restarted.ensure_daily()
        self.assertEqual(len(restarted.list()['items']), 1)
        self.clock += timedelta(days=1)
        restarted.ensure_daily()
        self.assertEqual(len(restarted.list()['items']), 2)

    def test_restore_is_complete_and_creates_recoverable_safety_copy(self):
        item = self.store.create('assets', {'name': 'original', 'kind': 'folder'})
        snapshot = self.manager.create()
        self.store.update('assets', item['id'], {'name': 'changed'})
        self.manager.restore(snapshot['id'])
        self.assertEqual(self.store.state()['assets'][0]['name'], 'original')
        safety = next(entry for entry in self.manager.list()['items'] if entry['reason'] == 'before_restore')
        self.assertEqual(self.manager.download(safety['id'])['assets'][0]['name'], 'changed')

    def test_failed_storage_restore_keeps_data_and_safety_snapshot(self):
        self.store.create('assets', {'name': 'original', 'kind': 'file'})
        snapshot = self.manager.create()
        self.store.create('assets', {'name': 'new', 'kind': 'file'})
        before = self.store.state()
        with patch.object(self.store, '_insert', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                self.manager.restore(snapshot['id'])
        self.assertEqual(self.store.state(), before)
        valid = self.manager.create()
        with patch('backend.snapshots.os.replace', side_effect=OSError('temporary')):
            with self.assertRaises(StoreError):
                self.manager.create()
        listing = self.manager.list()
        self.assertFalse(listing['enabled'])
        self.assertIn(valid['id'], [entry['id'] for entry in listing['items']])
        self.manager.create()
        self.assertTrue(self.manager.list()['enabled'])
        self.assertTrue(any(entry['reason'] == 'before_restore' for entry in self.manager.list()['items']))

    def test_retention_only_removes_owned_valid_files(self):
        directory = self.root / 'snapshots'
        directory.mkdir()
        (directory / 'unrelated.json.gz').write_bytes(b'keep')
        for _ in range(34):
            self.manager.create()
            self.clock += timedelta(seconds=1)
        self.assertEqual(len(self.manager.list()['items']), 30)
        self.assertEqual((directory / 'unrelated.json.gz').read_bytes(), b'keep')
        for invalid in ('../ledger.sqlite3', '/outside', 'not-an-id'):
            with self.assertRaises(StoreError):
                self.manager.download(invalid)

    def test_corruption_and_write_failure_never_replace_live_data(self):
        self.store.create('assets', {'name': 'safe', 'kind': 'file'})
        snapshot = self.manager.create()
        before = self.store.state()
        target = self.root / 'snapshots' / ('databack-' + snapshot['id'] + '.json.gz')
        target.write_bytes(b'corrupt')
        with self.assertRaises(StoreError):
            self.manager.restore(snapshot['id'])
        self.assertEqual(self.store.state(), before)
        with patch('backend.snapshots.os.replace', side_effect=OSError('private-path')):
            with self.assertRaises(StoreError) as failure:
                self.manager.create()
        self.assertNotIn('private-path', str(failure.exception))
        self.assertEqual(self.store.state(), before)
