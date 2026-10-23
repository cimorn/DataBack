import copy
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.store import Store, StoreError


STAMP = "2026-10-11T08:00:00Z"


def imported_asset(name, kind="folder", **fields):
    return {
        "id": str(uuid.uuid4()), "name": name, "kind": kind, "source_path": "", "notes": "",
        "parent_id": None, "backup_interval_days": 30, "target_location_ids": [],
        "backup_enabled": True, "backup_target": False, "private": False, "created_at": STAMP, **fields,
    }


def import_payload(assets, schema=3):
    assets = copy.deepcopy(assets)
    for asset in assets:
        if schema < 3:
            del asset["backup_target"]
            asset.pop('private', None)
        if schema == 1:
            for field in ("parent_id", "backup_interval_days", "target_location_ids", "backup_enabled"):
                del asset[field]
    return {
        "format": "filebackup-ledger", "schema_version": schema, "exported_at": STAMP,
        "assets": assets, "versions": [], "locations": [], "records": [],
    }


def add_history(payload, asset, *, recorded=True):
    version = {"id": str(uuid.uuid4()), "asset_id": asset["id"], "label": "v1", "notes": "旧版本", "created_at": STAMP}
    payload["versions"].append(version)
    if not recorded:
        return version
    if not payload["locations"]:
        payload["locations"].append({"id": str(uuid.uuid4()), "name": "历史硬盘", "kind": "external",
                                     "base_path": "E:/历史", "notes": "旧位置", "created_at": STAMP})
    record = {"id": str(uuid.uuid4()), "version_id": version["id"], "location_id": payload["locations"][0]["id"],
              "backed_up_at": "2026-10-10T03:30:00Z", "stored_path": "stored-copy", "notes": "旧备份事实", "created_at": STAMP}
    payload["records"].append(record)
    return version


class BackupTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "targets.sqlite3"
        self.store = Store(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, name, kind="folder", **fields):
        return self.store.create("assets", {"name": name, "kind": kind, **fields})

    def record_payload(self, asset):
        version = self.store.create("versions", {"asset_id": asset["id"], "label": "v1"})
        location = self.store.create("locations", {"name": "硬盘 " + asset["name"], "kind": "external"})
        return {"version_id": version["id"], "location_id": location["id"], "backed_up_at": STAMP}

    def error(self, status, callback, message=None):
        with self.assertRaises(StoreError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_manual_file_and_folder_are_ordinary_at_every_depth(self):
        root = self.asset("Photos", source_path=r"Z:\not-mounted\Photos")
        branch = self.asset("Me", parent_id=root["id"])
        deep = self.asset("2026", parent_id=branch["id"])
        shallow = self.asset("Letter", "file", parent_id=root["id"])
        with patch("os.scandir", side_effect=AssertionError("must not read source contents")) as scan:
            detached = self.asset("Emotion", source_path=r"Z:\not-mounted\Photos\Emotion")
            scan.assert_not_called()
        for asset in (root, branch, deep, shallow, detached):
            self.assertIs(asset.get("backup_target"), False)
            self.assertIs(asset["backup_enabled"], True)
        self.assertIsNone(detached["parent_id"])

    def test_backup_target_requires_exact_bool_create_and_edit_without_mutation(self):
        asset = self.asset("普通文件", "file")
        before = self.store.state()
        for value in (0, 1, -1, 0.0, 1.0, "true", "false", None, [], {}):
            with self.subTest(value=value):
                self.error(400, lambda v=value: self.asset("非法标记", backup_target=v))
                self.error(400, lambda v=value: self.store.update("assets", asset["id"], {"backup_target": v}))
                self.assertEqual(self.store.state(), before)

    def test_mark_cancel_and_restore_preserve_policy_actual_history_and_storage_type(self):
        asset = self.asset("Letter", "file", backup_target=True, backup_enabled=False, backup_interval_days=7)
        payload = self.record_payload(asset)
        asset = self.store.update("assets", asset["id"], {"target_location_ids": [payload["location_id"]]})
        record = self.store.create("records", payload)
        unmarked = self.store.update("assets", asset["id"], {"backup_target": False})
        self.assertEqual(unmarked, {**asset, "backup_target": False})
        self.assertEqual(Store(self.db).state(), self.store.state())
        self.assertEqual(self.store.state()["records"], [record])
        exported = self.store.export()
        self.assertEqual(exported["schema_version"], 3)
        self.assertIs(exported["assets"][0]["backup_target"], False)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT backup_target, typeof(backup_target) FROM assets").fetchone(), (0, "integer"))
            column = next(row for row in conn.execute("PRAGMA table_info(assets)") if row[1] == "backup_target")
            self.assertEqual((column[2], column[3], column[4]), ("INTEGER", 1, "0"))
        self.assertEqual(self.store.update("assets", asset["id"], {"backup_target": True}), asset)
        self.assertEqual(self.store.state()["records"], [record])

    def test_unmarked_leaf_rejects_single_and_batch_records_with_conflict(self):
        asset = self.asset("普通末级文件", "file")
        payload = self.record_payload(asset)
        before = self.store.state()
        self.error(409, lambda: self.store.create("records", payload), "设为备份对象")
        self.error(409, lambda: self.store.create_batch([payload]), "设为备份对象")
        self.assertEqual(self.store.state(), before)

    def test_first_backup_can_mark_and_save_atomically(self):
        asset = self.asset("首次备份", backup_interval_days=17, notes="保留")
        payload = self.record_payload(asset)
        records = self.store.create_batch([payload], mark_asset_id=asset["id"])
        self.assertEqual(len(records), 1)
        after = self.store.state()
        self.assertEqual(after["assets"], [{**asset, "backup_target": True}])
        self.assertEqual(after["records"], records)

    def test_failed_combined_save_rolls_back_marker_and_every_record(self):
        asset = self.asset("失败不标记")
        payload = self.record_payload(asset)
        before = self.store.state()
        invalid = {**payload, "location_id": str(uuid.uuid4())}
        self.error(404, lambda: self.store.create_batch([payload, invalid], mark_asset_id=asset["id"]))
        self.assertEqual(self.store.state(), before)

    def test_combined_mark_rejects_other_assets_and_nonleaf_without_mutation(self):
        first = self.asset("第一个")
        second = self.asset("另一个")
        payload = self.record_payload(first)
        before = self.store.state()
        self.error(409, lambda: self.store.create_batch([payload], mark_asset_id=second["id"]))
        self.assertEqual(self.store.state(), before)
        self.asset("子项", "file", parent_id=first["id"])
        before = self.store.state()
        self.error(409, lambda: self.store.create_batch([payload], mark_asset_id=first["id"]))
        self.assertEqual(self.store.state(), before)

    def test_combined_mark_validates_id_and_payload_before_committing(self):
        asset = self.asset("验证")
        payload = self.record_payload(asset)
        before = self.store.state()
        for invalid in ("invalid", 0, [], {}):
            self.error(400, lambda value=invalid: self.store.create_batch([payload], mark_asset_id=value))
            self.assertEqual(self.store.state(), before)
        self.error(400, lambda: self.store.create_batch([{}], mark_asset_id=asset["id"]))
        self.assertEqual(self.store.state(), before)

    def test_mixed_batch_rolls_back_marked_record_before_unmarked_leaf(self):
        marked = self.asset("已标记", "file", backup_target=True)
        ordinary = self.asset("普通", "file")
        records = [self.record_payload(asset) for asset in (marked, ordinary)]
        before = self.store.state()
        self.error(409, lambda: self.store.create_batch(records), "设为备份对象")
        self.assertEqual(self.store.state(), before)

    def test_marked_leaf_with_disabled_reminder_allows_single_and_batch_records(self):
        asset = self.asset("手动备份", backup_target=True, backup_enabled=False)
        payload = self.record_payload(asset)
        first = self.store.create("records", payload)
        second = self.store.create_batch([{**payload, "backed_up_at": "2026-10-12T08:00:00Z"}])[0]
        self.assertEqual(self.store.state()["records"], [first, second])
        self.assertIs(self.store.state()["assets"][0]["backup_enabled"], False)

    def test_nonleaf_cannot_be_newly_marked_and_dormant_true_can_be_edited(self):
        ordinary = self.asset("普通目录")
        self.asset("子文件", "file", parent_id=ordinary["id"])
        before = self.store.state()
        self.error(409, lambda: self.store.update("assets", ordinary["id"], {"backup_target": True}), "末级")
        self.assertEqual(self.store.state(), before)
        marked = self.asset("曾经的备份文件夹", backup_target=True)
        payload = self.record_payload(marked)
        historical = self.store.create("records", payload)
        child = self.asset("后来添加的子项", "file", parent_id=marked["id"])
        self.assertIs(child["backup_target"], False)
        self.assertIs(self.store.update("assets", marked["id"], {"backup_target": True, "notes": "休眠标记保留"})["backup_target"], True)
        self.assertIs(self.store.update("assets", marked["id"], {"backup_enabled": False})["backup_enabled"], False)
        self.error(409, lambda: self.store.create("records", payload), "末级")
        self.assertEqual(self.store.update("records", historical["id"], {"notes": "目录旧记录可编辑"})["notes"], "目录旧记录可编辑")
        self.store.update("assets", marked["id"], {"backup_target": False})
        self.error(409, lambda: self.store.update("assets", marked["id"], {"backup_target": True}), "末级")
        self.store.delete("assets", child["id"])
        self.assertIs(self.store.update("assets", marked["id"], {"backup_target": True})["backup_target"], True)

    def test_ordinary_history_remains_editable_after_cancelled_mark(self):
        asset = self.asset("取消标记后保留历史", "file", backup_target=True)
        payload = self.record_payload(asset)
        record = self.store.create("records", payload)
        self.store.update("assets", asset["id"], {"backup_target": False})
        edited = self.store.update("records", record["id"], {"backed_up_at": "2026-10-12T08:00:00+08:00"})
        self.assertEqual(edited["backed_up_at"], "2026-10-12T00:00:00Z")
        self.assertIs(Store(self.db).state()["assets"][0]["backup_target"], False)

    def test_schema3_roundtrip_preserves_dormant_mark_and_unmarked_actual_history(self):
        parent = imported_asset("休眠目录", backup_target=True, backup_enabled=False)
        child = imported_asset("普通子文件", "file", parent_id=parent["id"])
        ordinary = imported_asset("取消标记的历史文件", "file", backup_target=False, backup_enabled=False)
        payload = import_payload([child, parent, ordinary])
        add_history(payload, parent)
        add_history(payload, ordinary)
        self.store.import_data(payload)
        expected = {key: payload[key] for key in ("assets", "versions", "locations", "records")}
        self.assertEqual(self.store.state(), expected)
        destination = Store(Path(self.temp.name) / "roundtrip.sqlite3")
        destination.import_data(self.store.export())
        self.assertEqual(destination.state(), expected)
        self.assertEqual(Store(destination.db_path).state(), expected)

    def test_schema3_requires_both_exact_booleans_and_rejects_partial_import_atomically(self):
        known = self.asset("已有普通项", "file")
        good = imported_asset("有效新项", "file", backup_target=True)
        before = self.store.state()
        for field in ("backup_target", "backup_enabled"):
            for value in ("missing", None, 0, 1, 0.0, "false", [], {}):
                with self.subTest(field=field, value=value):
                    malformed = imported_asset("无效新项", "file")
                    malformed[field] = value
                    if value == "missing":
                        del malformed[field]
                    self.error(400, lambda m=malformed: self.store.import_data(import_payload([good, m])))
                    bad_known = {**known, field: value}
                    if value == "missing":
                        del bad_known[field]
                    self.error(400, lambda k=bad_known: self.store.import_data(import_payload([good, k])))
                    self.assertEqual(self.store.state(), before)

    def test_old_schema_rejects_backup_target_field_without_mutation(self):
        asset = imported_asset("旧格式新项")
        for schema in (1, 2):
            for value in (True, False):
                with self.subTest(schema=schema, value=value):
                    payload = import_payload([asset], schema)
                    payload["assets"][0]["backup_target"] = value
                    self.error(400, lambda: self.store.import_data(payload))
                    self.assertEqual(self.store.state()["assets"], [])

    def test_old_schema_infers_only_actual_history_leaves_after_effective_graph(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                store = Store(Path(self.temp.name) / f"old-{schema}.sqlite3")
                parent = imported_asset("有历史的中间目录", source_path="D:/docs")
                child = imported_asset("有历史的叶文件", "file", source_path="D:/docs/file.pdf", parent_id=parent["id"], backup_enabled=False)
                version_only = imported_asset("只有版本", "file")
                policy_only = imported_asset("只有策略", backup_interval_days=7)
                ordinary = imported_asset("空目录")
                payload = import_payload([child, parent, version_only, policy_only, ordinary], schema)
                add_history(payload, parent)
                add_history(payload, child)
                add_history(payload, version_only, recorded=False)
                store.import_data(payload)
                assets = {entry["id"]: entry for entry in store.state()["assets"]}
                self.assertEqual(assets[child["id"]]["parent_id"], parent["id"])
                self.assertIs(assets[child["id"]].get("backup_target"), True)
                self.assertIs(assets[child["id"]]["backup_enabled"], schema == 1)
                for asset in (parent, version_only, policy_only, ordinary):
                    self.assertIs(assets[asset["id"]].get("backup_target"), False)
                for collection in ("versions", "locations", "records"):
                    self.assertEqual(store.state()[collection], payload[collection])

    def test_old_import_new_history_keeps_known_false_and_true_flags_authoritative(self):
        for schema in (1, 2, 3):
            with self.subTest(schema=schema):
                store = Store(Path(self.temp.name) / f"known-{schema}.sqlite3")
                ordinary = store.create("assets", {"name": "已有普通项", "kind": "file", "backup_enabled": False})
                marked = store.create("assets", {"name": "已有标记项", "kind": "file", "backup_target": True})
                payload = import_payload([{**ordinary, "backup_target": True, "backup_enabled": True},
                                          {**marked, "backup_target": False}], schema)
                add_history(payload, ordinary)
                store.import_data(payload)
                self.assertEqual(store.state()["assets"], [ordinary, marked])
                self.assertEqual(store.state()["records"], payload["records"])
                self.assertEqual(Store(store.db_path).state(), store.state())

    def test_old_import_effective_versions_ignore_known_incoming_asset_redirection(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                store = Store(Path(self.temp.name) / f"versions-{schema}.sqlite3")
                known = store.create("assets", {"name": "当前资料", "kind": "file"})
                version = store.create("versions", {"asset_id": known["id"], "label": "v1"})
                new = imported_asset("有误导版本关联但没有实际历史", "file")
                payload = import_payload([new], schema)
                add_history(payload, new)
                payload["versions"][0] = {**version, "asset_id": new["id"]}
                payload["records"][0]["version_id"] = version["id"]
                store.import_data(payload)
                assets = {entry["id"]: entry for entry in store.state()["assets"]}
                self.assertIs(assets[new["id"]].get("backup_target"), False)
                self.assertEqual(store.state()["versions"], [version])
                self.assertIs(assets[known["id"]]["backup_target"], False)

    def test_old_import_effective_records_ignore_known_incoming_version_redirection(self):
        for schema in (1, 2):
            with self.subTest(schema=schema):
                store = Store(Path(self.temp.name) / f"records-{schema}.sqlite3")
                known = store.create("assets", {"name": "当前资料", "kind": "file", "backup_target": True})
                version = store.create("versions", {"asset_id": known["id"], "label": "v1"})
                location = store.create("locations", {"name": "硬盘", "kind": "external"})
                record = store.create("records", {"version_id": version["id"], "location_id": location["id"], "backed_up_at": STAMP})
                new = imported_asset("仅有导入重复记录的错误关联", "file")
                payload = import_payload([new], schema)
                incoming_version = add_history(payload, new, recorded=False)
                payload["records"] = [{**record, "version_id": incoming_version["id"]}]
                store.import_data(payload)
                assets = {entry["id"]: entry for entry in store.state()["assets"]}
                self.assertIs(assets[new["id"]].get("backup_target"), False)
                self.assertEqual(store.state()["records"], [record])

    def test_schema3_invalid_graph_rolls_back_all_collections(self):
        parent = imported_asset("新目录", backup_target=True)
        child = imported_asset("新子文件", "file", parent_id=parent["id"])
        payload = import_payload([child, parent])
        add_history(payload, child)
        payload["assets"][1]["parent_id"] = child["id"]
        before = self.store.state()
        self.error(409, lambda: self.store.import_data(payload))
        self.assertEqual(self.store.state(), before)


class BackupTargetMigrationTests(unittest.TestCase):
    @staticmethod
    def create_old_database(db, payload, hierarchy):
        with closing(sqlite3.connect(db)) as conn, conn:
            suffix = (", parent_id TEXT REFERENCES assets(id) DEFERRABLE INITIALLY DEFERRED, "
                      "backup_interval_days INTEGER NOT NULL DEFAULT 30, target_location_ids TEXT NOT NULL DEFAULT '[]', "
                      "backup_enabled INTEGER NOT NULL DEFAULT 1") if hierarchy else ""
            conn.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
                         "source_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL" + suffix + ")")
            conn.execute("CREATE TABLE versions (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, "
                         "label TEXT NOT NULL, label_key TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(asset_id, label_key))")
            conn.execute("CREATE TABLE locations (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, "
                         "kind TEXT NOT NULL, base_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), "
                         "location_id TEXT NOT NULL REFERENCES locations(id), backed_up_at TEXT NOT NULL, "
                         "stored_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL)")
            for collection in ("assets", "versions", "locations", "records"):
                for entry in payload[collection]:
                    values = dict(entry)
                    if collection == "assets":
                        del values["backup_target"]
                        values.pop('private', None)
                        if hierarchy:
                            values["target_location_ids"] = json.dumps(values["target_location_ids"])
                            values["backup_enabled"] = int(values["backup_enabled"])
                        else:
                            for field in ("parent_id", "backup_interval_days", "target_location_ids", "backup_enabled"):
                                del values[field]
                    if collection == "versions":
                        values["label_key"] = values["label"].casefold()
                    if collection == "locations":
                        values["name_key"] = values["name"].casefold()
                    conn.execute(f"INSERT INTO {collection} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))

    def test_first_old_database_migration_marks_actual_leaves_after_parent_inference_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "original.sqlite3"
            parent = imported_asset("有记录但推断为父目录", source_path="D:/docs", notes="原始目录备注")
            child = imported_asset("实际历史叶节点", "file", source_path="D:/docs/report.pdf")
            version_only = imported_asset("只有版本的叶节点", "file", source_path="D:/docs/version.pdf")
            ordinary = imported_asset("未登记的叶文件夹", source_path="D:/other")
            payload = import_payload([parent, child, version_only, ordinary])
            add_history(payload, parent)
            add_history(payload, child)
            add_history(payload, version_only, recorded=False)
            self.create_old_database(db, payload, hierarchy=False)
            with patch("os.scandir", side_effect=AssertionError("migration must not scan source paths")) as scan:
                store = Store(db)
                scan.assert_not_called()
            assets = {entry["id"]: entry for entry in store.state()["assets"]}
            self.assertEqual(assets[child["id"]]["parent_id"], parent["id"])
            self.assertIs(assets[child["id"]].get("backup_target"), True)
            for asset in (parent, version_only, ordinary):
                self.assertIs(assets[asset["id"]].get("backup_target"), False)
            old_fields = ("id", "name", "kind", "source_path", "notes", "created_at")
            self.assertEqual([{field: entry[field] for field in old_fields} for entry in store.state()["assets"]],
                             [{field: entry[field] for field in old_fields} for entry in payload["assets"]])
            for collection in ("versions", "locations", "records"):
                self.assertEqual(store.state()[collection], payload[collection])
            store.update("assets", child["id"], {"backup_target": False, "parent_id": None})
            before = store.state()
            self.assertEqual(Store(db).state(), before)
            self.assertIs(Store(db).state()["assets"][1]["backup_target"], False)

    def test_existing_hierarchy_migration_preserves_every_old_field_and_only_marks_actual_leaves(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "hierarchy.sqlite3"
            parent = imported_asset("手动父目录", source_path="D:/docs", backup_enabled=False, backup_interval_days=17)
            child = imported_asset("有记录子文件", "file", parent_id=parent["id"], backup_enabled=False, backup_interval_days=7)
            detached = imported_asset("手动脱离但路径嵌套", "file", source_path="D:/docs/detached.pdf", notes="手动根层保留")
            policy_only = imported_asset("无历史但有策略", backup_interval_days=2, backup_enabled=False)
            payload = import_payload([parent, child, detached, policy_only])
            add_history(payload, parent)
            add_history(payload, child)
            add_history(payload, detached)
            policy_only["target_location_ids"] = [payload["locations"][0]["id"]]
            payload["assets"][3] = policy_only
            self.create_old_database(db, payload, hierarchy=True)
            store = Store(db)
            expected = copy.deepcopy(payload)
            for entry in expected["assets"]:
                entry["backup_target"] = entry["id"] in (child["id"], detached["id"])
            expected = {key: expected[key] for key in ("assets", "versions", "locations", "records")}
            self.assertEqual(store.state(), expected)
            self.assertEqual(Store(db).state(), expected)
            self.assertIsNone(store.state()["assets"][2]["parent_id"])
            store.update("assets", detached["id"], {"backup_target": False})
            before = store.state()
            self.assertEqual(Store(db).state(), before)

    def test_failed_initial_migration_rolls_back_column_and_preserves_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "rollback.sqlite3"
            asset = imported_asset("原始资料", "file")
            payload = import_payload([asset])
            self.create_old_database(db, payload, hierarchy=False)
            with closing(sqlite3.connect(db)) as conn:
                before_columns = list(conn.execute("PRAGMA table_info(assets)"))
                before_rows = list(conn.execute("SELECT * FROM assets"))
            with patch("backend.store.infer_parents", side_effect=RuntimeError("migration failed")):
                with self.assertRaises(RuntimeError):
                    Store(db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(list(conn.execute("PRAGMA table_info(assets)")), before_columns)
                self.assertEqual(list(conn.execute("SELECT * FROM assets")), before_rows)


if __name__ == "__main__":
    unittest.main()
