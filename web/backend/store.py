"""SQLite persistence for backup facts; no file contents or cloud accounts."""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


from .validation import (
    COLLECTIONS, FIELDS, DEFAULTS, TIME_PATTERN, FIELD_NAMES, StoreError,
    utc_now, normalize_time, validate_id, infer_parents, validate_collection,
    validate_entry, validate_batch_mark, validate_asset_graph, validate_reorder,
    appended_order,
)


from .recovery import RecoveryMixin, make_trash


class Store(RecoveryMixin):
    def __init__(self, db_path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as conn:
            # Execute DDL individually: executescript would implicitly commit
            # and leave the schema migration outside this transaction.
            schema = """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                    source_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    label TEXT NOT NULL, label_key TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(asset_id, label_key)
                );
                CREATE TABLE IF NOT EXISTS locations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    base_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
                    location_id TEXT NOT NULL REFERENCES locations(id), backed_up_at TEXT NOT NULL,
                    stored_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_version ON records(version_id);
                CREATE INDEX IF NOT EXISTS records_location ON records(location_id);
            """
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            self._migrate_assets(conn)
            conn.execute('CREATE TABLE IF NOT EXISTS trash (id TEXT PRIMARY KEY, expires_at TEXT NOT NULL, payload TEXT NOT NULL)')

    def _migrate_assets(self, conn):
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
        additions = {
            "private": "INTEGER NOT NULL DEFAULT 0",
            "parent_id": "TEXT REFERENCES assets(id) DEFERRABLE INITIALLY DEFERRED",
            "backup_interval_days": "INTEGER NOT NULL DEFAULT 30",
            "target_location_ids": "TEXT NOT NULL DEFAULT '[]'",
            "backup_enabled": "INTEGER NOT NULL DEFAULT 1",
            "backup_target": "INTEGER NOT NULL DEFAULT 0",
            "manual_order": "INTEGER",
        }
        for field, definition in additions.items():
            if field not in columns:
                conn.execute(f"ALTER TABLE assets ADD COLUMN {field} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS assets_parent ON assets(parent_id)")
        # The column itself is the migration marker. Later restarts must keep
        # deliberate user edits, including detaching a formerly inferred child.
        if "parent_id" not in columns:
            assets = {row["id"]: self._public("assets", row) for row in conn.execute("SELECT * FROM assets")}
            infer_parents(assets, set(assets))
            conn.executemany("UPDATE assets SET parent_id = ? WHERE id = ?",
                             [(entry["parent_id"], entry["id"]) for entry in assets.values()])
        # Infer this flag only on the first migration, after any legacy parent
        # links are settled. Versions and policies alone are not backup facts.
        if "backup_target" not in columns:
            conn.execute("""
                UPDATE assets SET backup_target = 1
                WHERE NOT EXISTS (SELECT 1 FROM assets child WHERE child.parent_id = assets.id)
                  AND EXISTS (SELECT 1 FROM records r JOIN versions v ON r.version_id = v.id
                              WHERE v.asset_id = assets.id)
            """)

    @contextmanager
    def _transaction(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                # Each multi-query operation sees one coherent snapshot; reserving
                # the write slot also avoids read-then-write races in edit/import.
                conn.execute("BEGIN IMMEDIATE")
                yield conn
        except sqlite3.IntegrityError:
            raise StoreError("名称或版本名称已存在，或关联条目发生冲突。", 409) from None
        finally:
            conn.close()

    _collection = staticmethod(validate_collection)

    @staticmethod
    def _public(collection, row):
        result = {field: row[field] for field in ("id", *FIELDS[collection], "created_at")}
        if collection == "assets":
            result["target_location_ids"] = json.loads(result["target_location_ids"])
            result["backup_enabled"] = bool(result["backup_enabled"])
            result["backup_target"] = bool(result["backup_target"])
            result["private"] = bool(result["private"])
        if collection == "assets" and result["manual_order"] is None:
            del result["manual_order"]
        return result

    def _get(self, conn, collection, item_id):
        self._collection(collection)
        row = conn.execute(f"SELECT * FROM {collection} WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise StoreError("条目不存在，可能已被删除。", 404)
        return self._public(collection, row)

    _validate = staticmethod(validate_entry)

    def _references(self, conn, collection, entry, status=404, effective=None):
        def target_entry(target, item_id, field):
            row = (effective[target].get(item_id) if effective is not None else
                   conn.execute(f"SELECT * FROM {target} WHERE id = ?", (item_id,)).fetchone())
            if row is None:
                raise StoreError(f"{FIELD_NAMES[field]}不存在，请先添加对应条目。", status)
            return row

        if collection == "assets":
            if entry["parent_id"] is not None:
                parent = target_entry("assets", entry["parent_id"], "parent_id")
                if parent["kind"] != "folder":
                    raise StoreError("父条目必须是文件夹。", 409 if status == 409 else 400)
                if entry.get("id") == entry["parent_id"]:
                    raise StoreError("条目不能放在自身下面。", 409 if status == 409 else 400)
            for item_id in entry["target_location_ids"]:
                target_entry("locations", item_id, "target_location_ids")
        relations = {"versions": (("asset_id", "assets"),), "records": (("version_id", "versions"), ("location_id", "locations"))}
        for field, target in relations.get(collection, ()):
            target_entry(target, entry[field], field)

    _asset_graph = staticmethod(validate_asset_graph)

    @staticmethod
    def _stored_values(collection, entry):
        values = dict(entry)
        if collection == "assets":
            values["manual_order"] = entry.get("manual_order")
            values["target_location_ids"] = json.dumps(entry["target_location_ids"], separators=(",", ":"))
            values["backup_enabled"] = int(entry["backup_enabled"])
            values["backup_target"] = int(entry["backup_target"])
            values["private"] = int(entry["private"])
        elif collection == "versions":
            values["label_key"] = entry["label"].casefold()
        elif collection == "locations":
            values["name_key"] = entry["name"].casefold()
        return values

    def _insert(self, conn, collection, entry):
        values = self._stored_values(collection, entry)
        columns = tuple(values)
        conn.execute(f"INSERT INTO {collection} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(values.values()))
        return self._get(conn, collection, entry["id"])

    def _create(self, conn, collection, payload):
        entry = self._validate(collection, payload)
        if collection == "assets" and "manual_order" not in payload:
            position = appended_order([dict(row) for row in conn.execute("SELECT parent_id, manual_order FROM assets")], entry["parent_id"])
            if position is not None:
                entry["manual_order"] = position
        entry.update(id=str(uuid.uuid4()), created_at=utc_now())
        self._references(conn, collection, entry)
        if collection == "records":
            asset = conn.execute("SELECT a.id, a.backup_target FROM assets a JOIN versions v ON v.asset_id = a.id WHERE v.id = ?",
                                 (entry["version_id"],)).fetchone()
            if conn.execute("SELECT 1 FROM assets WHERE parent_id = ? LIMIT 1", (asset["id"],)).fetchone():
                raise StoreError("只有末级资料可以登记新的备份。", 409)
            if not asset["backup_target"]:
                raise StoreError("请先将资料设为备份对象。", 409)
        if collection == "assets":
            assets = {row["id"]: row for row in conn.execute("SELECT id, kind, parent_id FROM assets")}
            self._asset_graph({**assets, entry["id"]: entry})
        return self._insert(conn, collection, entry)

    def create(self, collection, payload):
        with self._transaction() as conn:
            return self._create(conn, collection, payload)

    def update(self, collection, item_id, payload):
        with self._transaction() as conn:
            current = self._get(conn, collection, item_id)
            if not isinstance(payload, dict) or set(payload) - (set(FIELDS[collection]) - {"private"}):
                raise StoreError("修改内容必须是对象，且只能包含可编辑字段。")
            writable = {key: current[key] for key in FIELDS[collection] if key in current and key != "private"}
            writable.update(payload)
            values = self._validate(collection, writable)
            if collection == "assets":
                values["private"] = current["private"]
            self._references(conn, collection, {**values, "id": item_id})
            if collection == "assets" and values["parent_id"] != current["parent_id"] and "manual_order" not in payload:
                values["manual_order"] = appended_order([dict(row) for row in conn.execute("SELECT id, parent_id, manual_order FROM assets") if row["id"] != item_id], values["parent_id"])
            if collection == "assets":
                has_children = conn.execute("SELECT 1 FROM assets WHERE parent_id = ? LIMIT 1", (item_id,)).fetchone()
                if values["kind"] == "file" and has_children:
                    raise StoreError("该文件夹仍有子项，不能改为文件。", 409)
                if has_children and values["backup_target"] and not current["backup_target"]:
                    raise StoreError("请标记需要备份的末级文件或文件夹。", 409)
                assets = {row["id"]: row for row in conn.execute("SELECT id, kind, parent_id FROM assets")}
                self._asset_graph({**assets, item_id: {**values, "id": item_id}})
            values = self._stored_values(collection, values)
            conn.execute(f"UPDATE {collection} SET {','.join(key + ' = ?' for key in values)} WHERE id = ?", (*values.values(), item_id))
            return self._get(conn, collection, item_id)

    def set_private(self, item_id, private):
        if type(private) is not bool:
            raise StoreError("隐私标记必须是布尔值。")
        with self._transaction() as conn:
            self._get(conn, "assets", item_id)
            conn.execute("UPDATE assets SET private = ? WHERE id = ?", (int(private), item_id))
            return {"changed": True}

    def reorder_assets(self, parent_id, ordered_ids):
        with self._transaction() as conn:
            assets = {row["id"]: dict(row) for row in conn.execute("SELECT id, kind, parent_id FROM assets")}
            ids = validate_reorder(assets, parent_id, ordered_ids)
            conn.executemany("UPDATE assets SET manual_order = ? WHERE id = ?", [(index, item_id) for index, item_id in enumerate(ids)])
            return {"ok": True}

    def delete(self, collection, item_id):
        with self._transaction() as conn:
            self._get(conn, collection, item_id)
            if collection == "assets" and conn.execute("SELECT 1 FROM assets WHERE parent_id = ? LIMIT 1", (item_id,)).fetchone():
                raise StoreError("该文件夹仍有子项，请先移动或删除子项。", 409)
            if collection == "locations":
                if any(item_id in json.loads(row[0]) for row in conn.execute("SELECT target_location_ids FROM assets")):
                    raise StoreError("该位置仍被资料选作目标备份位置，请先取消选择。", 409)
            queries = {
                "assets": "SELECT 1 FROM records r JOIN versions v ON r.version_id = v.id WHERE v.asset_id = ? LIMIT 1",
                "versions": "SELECT 1 FROM records WHERE version_id = ? LIMIT 1",
                "locations": "SELECT 1 FROM records WHERE location_id = ? LIMIT 1",
            }
            if collection in queries and conn.execute(queries[collection], (item_id,)).fetchone():
                raise StoreError("该条目仍有关联备份记录，请先删除相关记录。", 409)
            tombstone = make_trash(self._state(conn), collection, item_id)
            conn.execute('INSERT INTO trash (id, expires_at, payload) VALUES (?, ?, ?)',
                         (tombstone['id'], tombstone['expires_at'], json.dumps(tombstone, ensure_ascii=False)))
            conn.execute(f"DELETE FROM {collection} WHERE id = ?", (item_id,))
            return {"deleted": True, "trash_id": tombstone['id']}

    def _run_recovery(self, callback):
        with self._transaction() as conn:
            return callback(conn)

    def _replace_fields(self, conn, collection, item_id, entry):
        values = self._stored_values(collection, entry)
        conn.execute(f"UPDATE {collection} SET {','.join(key + ' = ?' for key in values)} WHERE id = ?", (*values.values(), item_id))

    def _read_trash(self, conn):
        return [json.loads(row['payload']) for row in conn.execute('SELECT payload FROM trash ORDER BY rowid DESC')]

    def _prune_trash(self, conn, now):
        conn.execute('DELETE FROM trash WHERE expires_at <= ?', (now,))

    def _remove_trash(self, conn, trash_id):
        conn.execute('DELETE FROM trash WHERE id = ?', (trash_id,))

    def _clear_state(self, conn):
        for collection in ('records', 'versions', 'assets', 'locations'):
            conn.execute(f'DELETE FROM {collection}')

    def _state(self, conn):
        # Stored UTC ISO values have either no fraction or exactly six digits.
        # A literal 'Z' sorts after '.', so compare whole seconds and a padded
        # microsecond field separately rather than sort the raw timestamp text.
        order = "substr(created_at,1,19), CASE WHEN substr(created_at,20,1)='.' THEN substr(created_at,21,6) ELSE '000000' END, rowid"
        return {collection: [self._public(collection, row) for row in conn.execute(f"SELECT * FROM {collection} ORDER BY {order}")] for collection in COLLECTIONS}

    def state(self):
        with self._transaction() as conn:
            return self._state(conn)

    def create_batch(self, records, *, mark_asset_id=None):
        if not isinstance(records, list) or not records:
            raise StoreError("请至少登记一条备份记录。")
        with self._transaction() as conn:
            if mark_asset_id is not None:
                validate_batch_mark(records, mark_asset_id, lambda collection, item_id: self._get(conn, collection, item_id))
                conn.execute("UPDATE assets SET backup_target = 1 WHERE id = ?", (mark_asset_id,))
            return [self._create(conn, "records", payload) for payload in records]

    def export(self):
        return {"format": "filebackup-ledger", "schema_version": 3, "exported_at": utc_now(), **self.state()}

    def import_data(self, payload):
        expected = {"format", "schema_version", "exported_at", *COLLECTIONS}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise StoreError("导入文件格式不完整，或包含不支持的字段。")
        if payload["format"] != "filebackup-ledger" or type(payload["schema_version"]) is not int or payload["schema_version"] not in (1, 2, 3):
            raise StoreError("该文件不是受支持的台账导出文件（格式版本 1、2 或 3）。")
        normalize_time(payload["exported_at"], "created_at")
        incoming, seen = {}, set()
        for collection in COLLECTIONS:
            if not isinstance(payload[collection], list):
                raise StoreError("导入文件中的各类条目必须是数组。")
            incoming[collection] = []
            for raw in payload[collection]:
                entry = self._validate(collection, raw, importing=True, schema_version=payload["schema_version"])
                if entry["id"] in seen:
                    raise StoreError("导入文件包含重复的条目 ID。")
                seen.add(entry["id"])
                incoming[collection].append(entry)
        added = {collection: 0 for collection in COLLECTIONS}
        with self._transaction() as conn:
            existing = {collection: {entry["id"]: entry for entry in entries} for collection, entries in self._state(conn).items()}
            effective = {collection: dict(entries) for collection, entries in existing.items()}
            for collection in COLLECTIONS:
                for entry in incoming[collection]:
                    if any(entry["id"] in existing[other] for other in COLLECTIONS if other != collection):
                        raise StoreError("导入条目 ID 与当前其他类型的条目冲突。", 409)
                    if entry["id"] not in existing[collection]:
                        effective[collection][entry["id"]] = entry
            if payload["schema_version"] == 1:
                infer_parents(effective["assets"], set(effective["assets"]) - set(existing["assets"]))
            # Validate the final graph against current known entries, whose fields
            # are never overwritten. No array order can affect reference validity.
            for collection in COLLECTIONS:
                for entry in incoming[collection]:
                    self._references(conn, collection, entry, status=409, effective=effective)
            self._asset_graph(effective["assets"], status=409)
            if payload["schema_version"] in (1, 2):
                parents = {entry["parent_id"] for entry in effective["assets"].values() if entry["parent_id"] is not None}
                recorded_assets = {effective["versions"][record["version_id"]]["asset_id"]
                                   for record in effective["records"].values()}
                for entry in incoming["assets"]:
                    if entry["id"] not in existing["assets"]:
                        entry["backup_target"] = entry["id"] in recorded_assets and entry["id"] not in parents
            for collection in COLLECTIONS:
                for entry in incoming[collection]:
                    if entry["id"] not in existing[collection]:
                        self._insert(conn, collection, entry)
                        added[collection] += 1
            return {"added": added}
