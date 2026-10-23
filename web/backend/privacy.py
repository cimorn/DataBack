"""Short-lived privacy capabilities and request-scoped visibility/write guards."""

import hashlib
import secrets
import threading
import time
from collections import OrderedDict

from .auth import SessionAuth, valid_password
from .store import COLLECTIONS, StoreError, validate_reorder


PRIVACY_COOKIE_NAME = 'databack_privacy'
PRIVACY_SECONDS = 30 * 60


class PrivacyAuth:
    def __init__(self, password, *, clock=time.time, max_sessions=256):
        self._clock = clock
        self._password = SessionAuth(password, secrets.token_urlsafe(48), clock=clock)
        self._sessions = OrderedDict()
        self._max_sessions = max_sessions
        self._lock = threading.RLock()

    def verify(self, password, peer):
        # Reuse the normal bounded per-peer/global failure windows and constant
        # time password comparison; a successful flag change grants no session.
        if not valid_password(password):
            password = '\x00'  # invalid inputs still consume a failed attempt
        try:
            token = self._password.login(password, peer)
        except StoreError as error:
            if error.status == 401:
                return False
            raise
        self._password.revoke(token)
        return True

    @staticmethod
    def _binding(main_token):
        return hashlib.sha256((main_token or 'local-loopback').encode('utf-8')).digest()

    def _prune(self):
        now = self._clock()
        for token, (_, expiry, _) in list(self._sessions.items()):
            if expiry <= now:
                del self._sessions[token]

    def grant(self, main_token):
        with self._lock:
            self._prune()
            token = secrets.token_urlsafe(32)
            proof = secrets.token_urlsafe(32)
            self._sessions[token] = (self._binding(main_token), self._clock() + PRIVACY_SECONDS, self._binding(proof))
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return token, proof

    def authenticated(self, token, main_token, proof=None):
        with self._lock:
            self._prune()
            value = self._sessions.get(token)
            return (isinstance(proof, str) and len(proof) == 43 and value is not None
                    and value[0] == self._binding(main_token) and value[2] == self._binding(proof))

    def revoke(self, token):
        with self._lock:
            self._prune()
            self._sessions.pop(token, None)

    def revoke_main(self, main_token):
        with self._lock:
            self._prune()
            binding = self._binding(main_token)
            for token, (current, _, _) in list(self._sessions.items()):
                if current == binding:
                    del self._sessions[token]

    @staticmethod
    def cookie(token=None, *, secure=True, clear=False):
        value = f'{PRIVACY_COOKIE_NAME}={"" if clear else token}; Path=/; Max-Age={0 if clear else PRIVACY_SECONDS}; HttpOnly; SameSite=Strict'
        return value + ('; Secure' if secure else '')


class PrivacyStore:
    """Enforce a coherent privacy snapshot while the server mutation lock is held."""

    def __init__(self, store, unlocked):
        self.store = store
        self.unlocked = unlocked
        self.snapshot = store.state()
        self.entries = {collection: {item['id']: item for item in items}
                        for collection, items in self.snapshot.items()}
        self.hidden = {collection: set() for collection in COLLECTIONS}
        self.ancestors = set()
        self.protected_locations = set()
        if unlocked:
            return
        assets = self.entries['assets']
        # Walk each ancestry once, propagating the nearest known visibility.
        visibility = {}
        for item_id in assets:
            cursor, path, seen = item_id, [], set()
            while cursor in assets and cursor not in visibility and cursor not in seen:
                path.append(cursor)
                seen.add(cursor)
                cursor = assets[cursor].get('parent_id')
            private = visibility.get(cursor, False)
            for current in reversed(path):
                private = private or assets[current].get('private', False)
                visibility[current] = private
        self.hidden['assets'] = {item_id for item_id, private in visibility.items() if private}
        self.hidden['versions'] = {item['id'] for item in self.snapshot['versions'] if item['asset_id'] in self.hidden['assets']}
        self.hidden['records'] = {item['id'] for item in self.snapshot['records'] if item['version_id'] in self.hidden['versions']}
        visible_locations = set()
        for item in self.snapshot['assets']:
            target = self.protected_locations if item['id'] in self.hidden['assets'] else visible_locations
            target.update(item['target_location_ids'])
        for item in self.snapshot['records']:
            target = self.protected_locations if item['id'] in self.hidden['records'] else visible_locations
            target.add(item['location_id'])
        self.hidden['locations'] = self.protected_locations - visible_locations
        for item_id in self.hidden['assets']:
            cursor = assets[item_id].get('parent_id')
            while cursor in assets and cursor not in self.ancestors:
                self.ancestors.add(cursor)
                cursor = assets[cursor].get('parent_id')

    @staticmethod
    def deny():
        raise StoreError('条目不存在，可能已被删除。', 404)

    def _check(self, collection, item_id):
        if isinstance(item_id, str) and item_id in self.hidden[collection]:
            self.deny()

    def _mutation(self, collection, item_id):
        self._check(collection, item_id)
        if ((collection == 'assets' and item_id in self.ancestors)
                or (collection == 'locations' and item_id in self.protected_locations)):
            self.deny()

    def _references(self, collection, payload):
        if not isinstance(payload, dict):
            return
        relations = {'assets': (('parent_id', 'assets'),), 'versions': (('asset_id', 'assets'),),
                     'records': (('version_id', 'versions'), ('location_id', 'locations'))}
        for field, target in relations.get(collection, ()):
            if field in payload:
                self._check(target, payload[field])
        if collection == 'assets' and isinstance(payload.get('target_location_ids'), list):
            for item_id in payload['target_location_ids']:
                self._check('locations', item_id)

    def state(self):
        return {collection: [item for item in items if item['id'] not in self.hidden[collection]]
                for collection, items in self.snapshot.items()}

    def _require_full_visibility(self, payload=None):
        if self.unlocked:
            return
        existing = any(item.get('private', False) for item in self.snapshot['assets'])
        incoming = (isinstance(payload, dict) and isinstance(payload.get('assets'), list)
                    and any(isinstance(item, dict) and item.get('private', False) for item in payload['assets']))
        if existing or incoming:
            raise StoreError('请先验证私密资料访问密码。', 403)

    def export(self):
        self._require_full_visibility()
        return self.store.export()

    def import_data(self, payload):
        self._require_full_visibility(payload)
        return self.store.import_data(payload)

    def create(self, collection, payload):
        self._references(collection, payload)
        if collection == 'locations' and isinstance(payload, dict):
            self.resolve_location(payload.get('name'))
        return self.store.create(collection, payload)

    def update(self, collection, item_id, payload):
        self._mutation(collection, item_id)
        self._references(collection, payload)
        if collection == 'locations' and isinstance(payload, dict):
            self.resolve_location(payload.get('name'))
        return self.store.update(collection, item_id, payload)

    def delete(self, collection, item_id):
        self._mutation(collection, item_id)
        return self.store.delete(collection, item_id)

    def bulk_assets(self, parent_id, kind, paths):
        self._check('assets', parent_id)
        return self.store.bulk_assets(parent_id, kind, paths, forbidden=self.hidden['assets'])

    def bulk_records(self, ids, changes):
        from .recovery import validate_bulk_records
        validate_bulk_records(ids, changes)
        for item_id in ids:
            self._mutation('records', item_id)
        if 'location_name' in changes:
            self.resolve_location(changes['location_name'])
        return self.store.bulk_records(ids, changes)

    def _trash_visible(self, item):
        if self.unlocked:
            return True
        if item.get('private'):
            return False
        active = self.entries['assets']
        deleted_ids = {entry['id'] for entry in item['entries']['assets']}
        # Recheck the *current* ancestry as well as the recorded privacy flag.
        # Missing dependencies remain hidden until the privacy view is open.
        for ancestor in item.get('ancestry', []):
            item_id = ancestor['id']
            if item_id in self.hidden['assets']:
                return False
            if item_id not in active and item_id not in deleted_ids:
                return False
        for record in item['entries']['records']:
            if record['version_id'] not in self.entries['versions'] or record['version_id'] in self.hidden['versions']:
                return False
            if record['location_id'] not in self.entries['locations'] or record['location_id'] in self.hidden['locations']:
                return False
        return True

    def trash(self):
        from .recovery import trash_public
        return [trash_public(item) for item in self.store.trash_entries() if self._trash_visible(item)]

    def restore_trash(self, trash_id):
        item = next((entry for entry in self.store.trash_entries() if entry['id'] == trash_id), None)
        if item is None or not self._trash_visible(item):
            self.deny()
        return self.store.restore_trash(trash_id)

    def create_batch(self, records, *, mark_asset_id=None):
        if mark_asset_id is not None:
            self._mutation('assets', mark_asset_id)
        for payload in records:
            self._references('records', payload)
        return self.store.create_batch(records, mark_asset_id=mark_asset_id)

    def resolve_location(self, name):
        if isinstance(name, str):
            for item in self.snapshot['locations']:
                if item['name'].casefold() == name.strip().casefold():
                    self._check('locations', item['id'])

    def reorder_assets(self, parent_id, ordered_ids):
        self._check('assets', parent_id)
        if isinstance(ordered_ids, list):
            for item_id in ordered_ids:
                self._check('assets', item_id)
        visible_assets = {item['id']: item for item in self.state()['assets']}
        ids = validate_reorder(visible_assets, parent_id, ordered_ids)
        siblings = [item for item in self.snapshot['assets'] if item.get('parent_id') == parent_id]
        if any(item['id'] in self.hidden['assets'] for item in siblings):
            siblings.sort(key=lambda item: (item.get('manual_order', float('inf')), item['kind'] != 'folder', item['name'].lower(), item['id']))
            replacements = iter(ids)
            ids = [item['id'] if item['id'] in self.hidden['assets'] else next(replacements) for item in siblings]
        return self.store.reorder_assets(parent_id, ids)
