"""Privacy boundaries against real isolated SQLite HTTP servers."""

import copy
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from backend.config import ServerConfig
from backend.server import make_server
from backend.store import Store, StoreError
from backend.privacy import PrivacyAuth


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = make_server(port=0, db_path=self.root / 'privacy.sqlite3', config=ServerConfig(port=0, privacy_password='fixture-privacy'))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookies = {}
        self.proof = None
        self.store = self.server.store
        self.parent = self.store.create('assets', {'name': 'ordinary parent', 'kind': 'folder'})
        self.folder = self.store.create('assets', {'name': 'secret folder', 'kind': 'folder', 'parent_id': self.parent['id']})
        self.child = self.store.create('assets', {'name': 'secret child', 'kind': 'file', 'parent_id': self.folder['id'], 'backup_target': True})
        self.visible = self.store.create('assets', {'name': 'visible', 'kind': 'file', 'backup_target': True})
        self.location = self.store.create('locations', {'name': 'secret disk', 'kind': 'external'})
        self.shared = self.store.create('locations', {'name': 'shared disk', 'kind': 'external'})
        self.store.update('assets', self.child['id'], {'target_location_ids': [self.location['id'], self.shared['id']]})
        self.store.update('assets', self.visible['id'], {'target_location_ids': [self.shared['id']]})
        self.version = self.store.create('versions', {'asset_id': self.child['id'], 'label': 'secret version'})
        self.public_version = self.store.create('versions', {'asset_id': self.visible['id'], 'label': 'public version'})
        self.record = self.store.create('records', {'version_id': self.version['id'], 'location_id': self.location['id'], 'backed_up_at': '2026-10-22T00:00:00Z', 'notes': 'secret history'})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, extra=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        headers = {'Content-Type': 'application/json', 'Origin': self.server.config.public_origin or f'http://127.0.0.1:{self.server.server_port}'}
        if self.server.config.auth_required:
            headers['Host'] = 'privacy.test'
        if self.proof:
            headers['X-Databack-Privacy'] = self.proof
        if self.cookies:
            headers['Cookie'] = '; '.join(f'{key}={value}' for key, value in self.cookies.items())
        headers.update(extra or {})
        conn.request(method, path, body=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read())
        for key, value in response.getheaders():
            if key.lower() == 'set-cookie':
                name, _, token = value.split(';')[0].partition('=')
                self.cookies[name] = token
        result = response.status, data, dict(response.getheaders())
        conn.close()
        return result

    def hide(self):
        status, data, _ = self.request('POST', f"/api/assets/{self.folder['id']}/privacy", {'password': 'fixture-privacy', 'private': True})
        self.assertEqual((status, data), (200, {'changed': True}))

    def unlock(self):
        result = self.request('POST', '/api/privacy/unlock', {'password': 'fixture-privacy'})
        self.assertEqual(result[0], 200)
        self.assertTrue(result[1]['unlocked'])
        self.assertIsInstance(result[1].get('token'), str)
        self.proof = result[1]['token']
        return result

    def test_capability_cookie_alone_cannot_unlock_another_tabs_reads(self):
        self.hide()
        self.unlock()
        proof, self.proof = self.proof, None
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})
        self.assertNotIn(self.folder['id'], {item['id'] for item in self.request('GET', '/api/state')[1]['assets']})
        self.proof = proof
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': True})

    def test_main_session_binding_and_logout_revoke_privacy_capability(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        config = ServerConfig(host='127.0.0.1', port=0, public_origin='https://privacy.test', allowed_hosts=frozenset({'privacy.test'}), privacy_password='fixture-privacy',
                              admin_password='main-admin-password!7X', session_secret='test-session-secret-0123456789-ABCDEF')
        self.server = make_server(port=0, db_path=self.root / 'privacy.sqlite3', config=config)
        self.store = self.server.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertEqual(self.request('GET', '/api/privacy')[0], 401)
        self.assertEqual(self.request('POST', '/api/login', {'password': config.admin_password})[0], 200)
        self.hide()
        self.unlock()
        captured = dict(self.cookies)
        self.assertEqual(self.request('POST', '/api/logout', {})[0], 200)
        self.assertEqual(self.request('POST', '/api/login', {'password': config.admin_password})[0], 200)
        self.cookies['databack_privacy'] = captured['databack_privacy']
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})
        self.cookies = captured
        self.assertEqual(self.request('GET', '/api/state')[0], 401)

    def test_password_capability_is_silent_revocable_and_expires(self):
        self.assertEqual(self.request('GET', '/api/privacy')[:2], (200, {'unlocked': False}))
        self.assertEqual(self.request('POST', '/api/privacy/unlock', {'password': 'wrong'})[:2], (200, {'unlocked': False}))
        _, _, headers = self.unlock()
        cookie = headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=1800', cookie)
        self.assertNotIn('Secure', cookie)  # isolated loopback HTTP remains usable
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': True})
        captured = dict(self.cookies)
        self.assertEqual(self.request('POST', '/api/privacy/unlock', {'password': 'wrong'})[:2], (200, {'unlocked': False}))
        self.cookies = captured
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})
        self.unlock()
        self.assertEqual(self.request('POST', '/api/privacy/lock', {})[:2], (200, {'unlocked': False}))
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})

    def test_locked_state_hides_tree_history_and_exclusive_locations(self):
        self.hide()
        state = self.request('GET', '/api/state')[1]
        self.assertEqual({x['id'] for x in state['assets']}, {self.parent['id'], self.visible['id']})
        self.assertEqual([x['id'] for x in state['versions']], [self.public_version['id']])
        self.assertEqual(state['records'], [])
        self.assertEqual([x['id'] for x in state['locations']], [self.shared['id']])
        self.assertNotIn('secret', json.dumps(state))
        self.unlock()
        self.assertEqual(self.request('GET', '/api/state')[1], self.store.state())

    def test_hidden_direct_mutations_and_references_are_denied_atomically(self):
        self.hide()
        before = self.store.state()
        record = {'version_id': self.version['id'], 'location_id': self.shared['id'], 'backed_up_at': '2026-10-22T00:00:00Z'}
        attempts = [
            ('PUT', f"/api/assets/{self.child['id']}", {'notes': 'changed'}),
            ('DELETE', f"/api/versions/{self.version['id']}", {}),
            ('PUT', f"/api/records/{self.record['id']}", {'notes': 'changed'}),
            ('POST', '/api/assets', {'name': 'new', 'kind': 'file', 'parent_id': self.folder['id']}),
            ('POST', '/api/versions', {'asset_id': self.child['id'], 'label': 'new'}),
            ('POST', '/api/records', record),
            ('POST', '/api/records/batch', {'records': [record]}),
            ('POST', '/api/records/batch', {'records': [record], 'mark_asset_id': self.child['id']}),
            ('POST', '/api/locations/resolve', {'name': self.location['name']}),
            ('POST', '/api/locations', {'name': self.location['name'], 'kind': 'other'}),
            ('PUT', f"/api/locations/{self.shared['id']}", {'name': self.location['name']}),
            ('PUT', f"/api/assets/{self.visible['id']}", {'target_location_ids': [self.location['id']]}),
        ]
        for method, path, payload in attempts:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, payload)[0], 404)
                self.assertEqual(self.store.state(), before)

    def test_locked_ancestor_and_shared_location_writes_cannot_damage_hidden_data(self):
        self.hide()
        before = self.store.state()
        for collection, item_id, payload in (
            ('assets', self.parent['id'], {'name': 'renamed'}),
            ('assets', self.parent['id'], {'parent_id': None}),
            ('locations', self.shared['id'], {'name': 'renamed shared'}),
        ):
            self.assertEqual(self.request('PUT', f'/api/{collection}/{item_id}', payload)[0], 404)
        self.assertEqual(self.request('DELETE', f"/api/assets/{self.parent['id']}", {})[0], 404)
        self.assertEqual(self.store.state(), before)

    def test_private_flags_require_independent_password_and_generic_writes_reject_flag(self):
        self.hide()
        self.unlock()
        for item_id in (self.folder['id'], str(uuid.uuid4())):
            self.assertEqual(self.request('POST', f'/api/assets/{item_id}/privacy', {'password': 'wrong', 'private': False})[:2], (200, {'changed': False}))
        self.assertTrue(next(x for x in self.store.state()['assets'] if x['id'] == self.folder['id'])['private'])
        self.assertEqual(self.request('PUT', f"/api/assets/{self.folder['id']}", {'private': False})[0], 400)
        self.assertEqual(self.request('POST', '/api/assets', {'name': 'bypass', 'kind': 'folder', 'private': True})[0], 400)
        self.assertEqual(self.request('POST', f"/api/assets/{self.folder['id']}/privacy", {'password': 'fixture-privacy', 'private': False})[:2], (200, {'changed': True}))
        self.assertFalse(next(x for x in self.store.state()['assets'] if x['id'] == self.folder['id'])['private'])

    def test_export_and_import_require_unlock_when_either_contains_private_data(self):
        self.hide()
        snapshot = self.store.export()
        self.assertEqual(self.request('GET', '/api/export')[0], 403)
        self.assertEqual(self.request('POST', '/api/import', snapshot)[0], 403)
        self.unlock()
        self.assertEqual(self.request('GET', '/api/export')[0], 200)
        self.assertEqual(self.request('POST', '/api/import', snapshot)[0], 200)

    def test_visible_reorder_preserves_hidden_sibling_slot(self):
        self.store.update('assets', self.folder['id'], {'parent_id': None})
        self.store.reorder_assets(None, [self.parent['id'], self.folder['id'], self.visible['id']])
        self.hide()
        self.assertEqual(self.request('POST', '/api/assets/reorder', {'parent_id': None, 'ordered_ids': [self.visible['id'], self.parent['id']]})[0], 200)
        positions = {x['id']: x['manual_order'] for x in self.store.state()['assets'] if x['parent_id'] is None}
        self.assertEqual(positions, {self.parent['id']: 2, self.folder['id']: 1, self.visible['id']: 0})

    def test_visible_reorder_legacy_fallback_uses_lower_codepoint_order(self):
        self.store.update('assets', self.folder['id'], {'parent_id': None, 'name': 'ß'})
        self.store.update('assets', self.parent['id'], {'name': 't'})
        self.store.update('assets', self.visible['id'], {'name': 'z', 'kind': 'folder'})
        self.hide()
        self.assertEqual(self.request('POST', '/api/assets/reorder', {'parent_id': None, 'ordered_ids': [self.visible['id'], self.parent['id']]})[0], 200)
        positions = {x['id']: x['manual_order'] for x in self.store.state()['assets'] if x['parent_id'] is None}
        self.assertEqual(positions, {self.parent['id']: 1, self.folder['id']: 2, self.visible['id']: 0})

    def test_visible_reorder_chinese_fallback_keeps_hidden_codepoint_slot(self):
        self.store.update('assets', self.folder['id'], {'parent_id': None, 'name': '中'})
        self.store.update('assets', self.parent['id'], {'name': '阿'})
        self.store.update('assets', self.visible['id'], {'name': '李', 'kind': 'folder'})
        self.hide()
        self.assertEqual(self.request('POST', '/api/assets/reorder', {'parent_id': None, 'ordered_ids': [self.parent['id'], self.visible['id']]})[0], 200)
        positions = {x['id']: x['manual_order'] for x in self.store.state()['assets'] if x['parent_id'] is None}
        self.assertEqual(positions, {self.parent['id']: 1, self.folder['id']: 0, self.visible['id']: 2})

    def test_explicit_lock_revokes_all_capabilities_for_current_main_session(self):
        self.unlock()
        first_cookies, first_proof = dict(self.cookies), self.proof
        self.cookies = {}
        self.proof = None
        self.unlock()
        self.request('POST', '/api/privacy/lock', {})
        self.cookies, self.proof = first_cookies, first_proof
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})

    def test_privacy_failure_budget_uses_socket_peer_even_with_forwarded_header(self):
        for index in range(5):
            self.assertEqual(self.request('POST', '/api/privacy/unlock', {'password': 'wrong'}, {'X-Forwarded-For': f'192.0.2.{index}'})[0], 200)
        self.assertEqual(self.request('POST', '/api/privacy/unlock', {'password': 'fixture-privacy'}, {'X-Forwarded-For': '192.0.2.99'})[0], 429)

    def test_lock_or_failed_unlock_revokes_before_write_throttle_rejects(self):
        from backend.security import PEER_RULES
        self.unlock()
        self.server.submissions._peer_rules = {**PEER_RULES, 'write': ((1, 60),)}
        self.assertEqual(self.request('POST', '/api/privacy/unlock', {'password': 'wrong'})[0], 429)
        self.assertEqual(self.request('GET', '/api/privacy')[1], {'unlocked': False})

    def test_private_import_into_empty_server_requires_unlock(self):
        from backend.storage import create_store
        self.hide()
        snapshot = self.store.export()
        fresh = create_store(self.root / 'empty.sqlite3')
        self.server.store = fresh
        self.assertEqual(self.request('POST', '/api/import', snapshot)[0], 403)
        self.assertEqual(fresh.state(), {key: [] for key in ('assets', 'versions', 'locations', 'records')})


class PrivacyStorageTests(unittest.TestCase):
    def test_migration_cli_old_snapshot_normalizes_private_before_summary(self):
        from scripts.migrate_to_mongo import canonical_summary, load_snapshot
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'old.sqlite3')
            store.create('assets', {'name': 'legacy', 'kind': 'folder'})
            payload = store.export()
            payload['assets'][0].pop('private')
            path = Path(directory) / 'old.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            loaded = load_snapshot(path)
            self.assertIs(loaded['assets'][0].get('private'), False)
            self.assertEqual(canonical_summary(loaded), canonical_summary(store.state()))

    def test_mongo_legacy_document_has_default_without_changing_original(self):
        from backend.mongo_store import MongoStore
        from backend.store import validate_entry
        row = validate_entry('assets', {'name': 'legacy', 'kind': 'folder'})
        row.update(id=str(uuid.uuid4()), created_at='2026-10-22T00:00:00Z')
        row.pop('private')
        before = copy.deepcopy(row)
        self.assertIs(MongoStore._public('assets', row)['private'], False)
        self.assertEqual(row, before)

    def test_capability_expiry_binding_and_memory_are_bounded(self):
        now = [1000.0]
        auth = PrivacyAuth('fixture-privacy', clock=lambda: now[0], max_sessions=2)
        token, proof = auth.grant('main-one')
        self.assertTrue(auth.authenticated(token, 'main-one', proof))
        self.assertFalse(auth.authenticated(token, 'main-two', proof))
        self.assertFalse(auth.authenticated(token, 'main-one', None))
        now[0] += 1800
        self.assertFalse(auth.authenticated(token, 'main-one', proof))
        for _ in range(5):
            auth.grant('main-one')
        self.assertEqual(len(auth._sessions), 2)
        auth.revoke_main('main-one')
        self.assertEqual(len(auth._sessions), 0)

    def test_default_migration_and_schema3_optional_private_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'storage.sqlite3'
            store = Store(path)
            asset = store.create('assets', {'name': 'folder', 'kind': 'folder'})
            self.assertIs(asset.get('private'), False)
            store.set_private(asset['id'], True)
            self.assertTrue(Store(path).state()['assets'][0]['private'])
            exported = store.export()
            restored = Store(Path(directory) / 'restored.sqlite3')
            restored.import_data(exported)
            self.assertTrue(restored.state()['assets'][0]['private'])
            exported['assets'][0].pop('private')
            old = Store(Path(directory) / 'old.sqlite3')
            old.import_data(exported)
            self.assertFalse(old.state()['assets'][0]['private'])
            for value in (1, 'true', None):
                bad = copy.deepcopy(exported)
                bad['assets'][0]['private'] = value
                with self.assertRaises(StoreError):
                    old.import_data(bad)

    def test_password_config_is_optional_validated_and_hidden(self):
        first = ServerConfig.from_env({}).privacy_password
        second = ServerConfig.from_env({}).privacy_password
        self.assertGreaterEqual(len(first), 32)
        self.assertNotEqual(first, second)
        self.assertNotIn(first, repr(ServerConfig.from_env({})))
        config = ServerConfig.from_env({'DATABACK_PRIVACY_PASSWORD': 'configured-secret'})
        self.assertNotIn('configured-secret', repr(config))
        self.assertEqual(config.privacy_password, 'configured-secret')
        for value in ('', None, 'bad\n', 'x' * 1025):
            with self.assertRaises(ValueError):
                ServerConfig(privacy_password=value)


if __name__ == '__main__':
    unittest.main()
