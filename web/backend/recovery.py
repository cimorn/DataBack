"""Atomic ledger helpers shared by SQLite and MongoDB; no source-file access."""

import uuid
from datetime import datetime, timedelta, timezone

from .validation import COLLECTIONS, FIELDS, StoreError, utc_now, validate_asset_graph, validate_entry, validate_id, normalize_time
from .security import MAX_BATCH_RECORDS, validate_submission_notes


def path_parts(paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 200:
        raise StoreError('单次请填写 1 到 200 条相对路径。')
    result = []
    expanded = set()
    for path in paths:
        if not isinstance(path, str) or len(path) > 8192:
            raise StoreError('目录路径无效或过长。')
        parts = [part.strip() for part in path.split('/')]
        if len(parts) > 500:
            raise StoreError('单次最多创建 500 个目录条目。', 413)
        if any(not part or part in ('.', '..') or len(part) > 200
               or any(ord(char) < 32 or ord(char) == 127 or char in '\\:' for char in part) for part in parts):
            raise StoreError('请使用 xx/xxx 相对路径，名称不能包含空层级、点路径、冒号或反斜杠。')
        folded = tuple(part.casefold() for part in parts)
        for depth in range(1, len(parts) + 1):
            expanded.add(folded[:depth])
            if len(expanded) > 500:
                raise StoreError('单次最多创建 500 个目录条目。', 413)
        result.append(parts)
    return result


def validate_bulk_records(ids, changes):
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH_RECORDS:
        raise StoreError(f'单次请选择 1 到 {MAX_BATCH_RECORDS} 条记录。')
    ids = [validate_id(item_id) for item_id in ids]
    if len(set(ids)) != len(ids):
        raise StoreError('不能重复选择同一条记录。')
    if not isinstance(changes, dict) or not changes or set(changes) - {'backed_up_at', 'location_name', 'notes'}:
        raise StoreError('请选择需要修改的时间、位置或备注。')
    validate_submission_notes(changes)
    if 'backed_up_at' in changes:
        normalize_time(changes['backed_up_at'])
    if 'location_name' in changes:
        validate_entry('locations', {'name': changes['location_name'], 'kind': 'other'})
    return ids


def full_state(payload):
    if (not isinstance(payload, dict) or set(payload) != {'format', 'schema_version', 'exported_at', *COLLECTIONS}
            or payload['format'] != 'filebackup-ledger' or type(payload['schema_version']) is not int or payload['schema_version'] != 3):
        raise StoreError('完整恢复需要格式版本 3 的台账快照。')
    normalize_time(payload['exported_at'], 'created_at')
    result, seen = {}, set()
    for collection in COLLECTIONS:
        if not isinstance(payload[collection], list):
            raise StoreError('快照内容无效。')
        result[collection] = []
        for raw in payload[collection]:
            item = validate_entry(collection, raw, importing=True)
            if item['id'] in seen:
                raise StoreError('快照包含重复 ID。', 409)
            seen.add(item['id'])
            result[collection].append(item)
    validate_asset_graph({item['id']: item for item in result['assets']}, status=409)
    return result


def make_trash(state, collection, item_id):
    maps = {key: {item['id']: item for item in state[key]} for key in COLLECTIONS}
    item = maps[collection][item_id]
    entries = {key: [] for key in COLLECTIONS}
    entries[collection].append(item)
    if collection == 'assets':
        entries['versions'] = [entry for entry in state['versions'] if entry['asset_id'] == item_id]
    asset = item if collection == 'assets' else maps['assets'].get(item.get('asset_id'))
    if collection == 'records':
        asset = maps['assets'][maps['versions'][item['version_id']]['asset_id']]
    ancestry, seen = [], set()
    while asset and asset['id'] not in seen:
        ancestry.append(dict(asset))
        seen.add(asset['id'])
        asset = maps['assets'].get(asset.get('parent_id'))
    now = utc_now()
    expiry = (datetime.fromisoformat(now.replace('Z', '+00:00')) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
    return {'id': str(uuid.uuid4()), 'collection': collection,
            'name': '/'.join(entry['name'] for entry in reversed(ancestry)) if ancestry else item.get('name', item.get('label', '记录')),
            'deleted_at': now, 'expires_at': expiry, 'entries': entries, 'ancestry': ancestry,
            'private': any(entry.get('private') for entry in ancestry)}


def trash_public(item):
    return {field: item[field] for field in ('id', 'collection', 'name', 'deleted_at', 'expires_at')}


class RecoveryMixin:
    """Adapters provide transaction, insertion, field replacement and trash IO."""

    def bulk_assets(self, parent_id, kind, paths, *, forbidden=None):
        parts_list = path_parts(paths)
        if kind not in ('file', 'folder'):
            raise StoreError('资料类型只能是文件或文件夹。')
        if parent_id is not None:
            validate_id(parent_id)
        def operation(tx):
            assets = self._state(tx)['assets']
            if parent_id is not None:
                parent = self._get(tx, 'assets', parent_id)
                if parent['kind'] != 'folder':
                    raise StoreError('所属目录必须是文件夹。', 409)
                if forbidden and parent_id in forbidden:
                    raise StoreError('条目不存在，可能已被删除。', 404)
            created, skipped = 0, 0
            for parts in parts_list:
                current = parent_id
                for index, name in enumerate(parts):
                    matches = [item for item in assets if item['parent_id'] == current and item['name'].casefold() == name.casefold()]
                    required = kind if index == len(parts) - 1 else 'folder'
                    if any(item['id'] in (forbidden or set()) for item in matches):
                        raise StoreError('条目不存在，可能已被删除。', 404)
                    if len(matches) > 1 or matches and matches[0]['kind'] != required:
                        raise StoreError('路径中已有不同类型或重名条目，请修改后重试。', 409)
                    if matches:
                        item = matches[0]
                        if index == len(parts) - 1:
                            skipped += 1
                    else:
                        item = self._create(tx, 'assets', {'name': name, 'kind': required, 'parent_id': current})
                        assets.append(item)
                        created += 1
                    current = item['id']
            return {'created': created, 'skipped': skipped}
        return self._run_recovery(operation)

    def bulk_records(self, ids, changes):
        ids = validate_bulk_records(ids, changes)
        def operation(tx):
            records = [self._get(tx, 'records', item_id) for item_id in ids]
            updates = {key: value for key, value in changes.items() if key != 'location_name'}
            if 'location_name' in changes:
                values = validate_entry('locations', {'name': changes['location_name'], 'kind': 'other'})
                location = next((item for item in self._state(tx)['locations'] if item['name'].casefold() == values['name'].casefold()), None)
                if location is None:
                    location = self._create(tx, 'locations', {'name': values['name'], 'kind': 'other'})
                updates['location_id'] = location['id']
            for record in records:
                values = validate_entry('records', {**{key: record[key] for key in FIELDS['records']}, **updates})
                self._references(tx, 'records', values)
                self._replace_fields(tx, 'records', record['id'], values)
            return {'updated': len(records)}
        return self._run_recovery(operation)

    def trash_entries(self):
        def operation(tx):
            self._prune_trash(tx, utc_now())
            return self._read_trash(tx)
        return self._run_recovery(operation)

    def trash(self):
        return [trash_public(item) for item in self.trash_entries()]

    def restore_trash(self, trash_id):
        validate_id(trash_id)
        def operation(tx):
            item = next((item for item in self._read_trash(tx) if item['id'] == trash_id), None)
            if item is None or item['expires_at'] <= utc_now():
                raise StoreError('回收记录不存在或已过期。', 404)
            current = self._state(tx)
            effective = {key: {entry['id']: entry for entry in current[key]} for key in COLLECTIONS}
            all_ids = {item_id for items in effective.values() for item_id in items}
            for key in COLLECTIONS:
                for entry in item['entries'][key]:
                    if entry['id'] in all_ids:
                        raise StoreError('原条目 ID 已存在，恢复已取消。', 409)
                    if key == 'assets' and any(existing['parent_id'] == entry['parent_id'] and existing['name'].casefold() == entry['name'].casefold() for existing in effective[key].values()):
                        raise StoreError('原目录已有同名条目，请先处理重名。', 409)
                    effective[key][entry['id']] = entry
                    all_ids.add(entry['id'])
            validate_asset_graph(effective['assets'], status=409)
            for key in COLLECTIONS:
                for entry in item['entries'][key]:
                    self._references(tx, key, entry, status=409, effective=effective)
                    self._insert(tx, key, entry)
            self._remove_trash(tx, trash_id)
            return {'restored': True}
        return self._run_recovery(operation)

    def restore_state(self, payload):
        incoming = full_state(payload)
        def operation(tx):
            effective = {key: {entry['id']: entry for entry in incoming[key]} for key in COLLECTIONS}
            for key in COLLECTIONS:
                for entry in incoming[key]:
                    self._references(tx, key, entry, status=409, effective=effective)
            self._clear_state(tx)
            for key in COLLECTIONS:
                for entry in incoming[key]:
                    self._insert(tx, key, entry)
            return {'restored': True}
        return self._run_recovery(operation)
