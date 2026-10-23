import unittest
import test_privacy


class RecoveryApiTests(unittest.TestCase):
    setUp = test_privacy.PrivacyTests.setUp
    tearDown = test_privacy.PrivacyTests.tearDown
    request = test_privacy.PrivacyTests.request
    unlock = test_privacy.PrivacyTests.unlock
    hide = test_privacy.PrivacyTests.hide

    def test_bulk_paths_and_edit_atomic_http_contract(self):
        status, data, _ = self.request('POST', '/api/assets/bulk', {'parent_id': self.parent['id'], 'kind': 'folder', 'paths': ['new/one', 'new/two', 'new/one']})
        self.assertEqual((status, data), (201, {'created': 3, 'skipped': 1}))
        status, data, _ = self.request('PUT', '/api/records/bulk', {'ids': [self.record['id']], 'changes': {'notes': 'edited', 'location_name': 'fresh disk'}})
        self.assertEqual((status, data), (200, {'updated': 1}))
        self.assertEqual(self.store.state()['records'][0]['notes'], 'edited')
        before = self.store.state()
        status, _, _ = self.request('PUT', '/api/records/bulk', {'ids': [self.record['id']], 'changes': {'notes': 'x' * 2001}})
        self.assertEqual(status, 400)
        self.assertEqual(self.store.state(), before)

    def test_trash_restore_visibility_and_unknown_references(self):
        status, removed, _ = self.request('DELETE', '/api/records/' + self.record['id'])
        self.assertEqual(status, 200)
        self.assertTrue(removed['deleted'])
        self.hide()
        self.assertEqual(self.request('GET', '/api/trash')[1], {'items': []})
        self.assertEqual(self.request('POST', '/api/trash/' + removed['trash_id'] + '/restore', {})[0], 404)
        self.unlock()
        self.assertEqual(len(self.request('GET', '/api/trash')[1]['items']), 1)
        self.assertEqual(self.request('POST', '/api/trash/' + removed['trash_id'] + '/restore', {})[:2], (200, {'restored': True}))

    def test_all_snapshot_actions_require_privacy_and_restore_complete_state(self):
        self.assertEqual(self.request('GET', '/api/snapshots')[0], 403)
        self.assertEqual(self.request('POST', '/api/snapshots', {})[0], 403)
        from backend.security import SubmissionGuard
        self.server.submissions = SubmissionGuard()
        self.unlock()
        status, snapshot, _ = self.request('POST', '/api/snapshots', {})
        self.assertEqual(status, 201, snapshot)
        self.assertEqual(snapshot['counts']['records'], 1)
        self.store.update('records', self.record['id'], {'notes': 'newer'})
        self.assertEqual(self.request('POST', '/api/snapshots/' + snapshot['id'] + '/restore', {})[:2], (200, {'restored': True}))
        self.assertEqual(self.store.state()['records'][0]['notes'], self.record['notes'])
        downloaded = self.request('GET', '/api/snapshots/' + snapshot['id'])[1]
        self.assertEqual(downloaded['format'], 'filebackup-ledger')
        self.assertEqual(downloaded['records'][0]['id'], self.record['id'])
        self.request('POST', '/api/privacy/lock', {})
        self.assertEqual(self.request('GET', '/api/snapshots/' + snapshot['id'])[0], 403)

    def test_bulk_record_budget_applies_weighted_and_large_requests_fail(self):
        version = self.version
        ids = [self.record['id']]
        for index in range(19):
            record = self.store.create('records', {'version_id': version['id'], 'location_id': self.shared['id'], 'backed_up_at': '2026-10-22T00:00:00Z'})
            ids.append(record['id'])
        self.assertEqual(self.request('PUT', '/api/records/bulk', {'ids': ids, 'changes': {'notes': 'batch'}})[0], 200)
        self.assertEqual(self.request('PUT', '/api/records/bulk', {'ids': ids[:1], 'changes': {'notes': 'too soon'}})[0], 429)
        self.assertEqual({record['notes'] for record in self.store.state()['records']}, {'batch'})
