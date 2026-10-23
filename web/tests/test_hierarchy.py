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


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "hierarchy.sqlite3"
        self.store = Store(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, name, kind="folder", **fields):
        return self.store.create("assets", {"name": name, "kind": kind, **fields})

    def location(self, name="硬盘"):
        return self.store.create("locations", {"name": name, "kind": "external"})

    def error(self, status, callback):
        with self.assertRaises(StoreError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        self.assertTrue(str(caught.exception))

    @staticmethod
    def imported_asset(name, kind="folder", **fields):
        return {
            "id": str(uuid.uuid4()), "name": name, "kind": kind,
            "source_path": "", "notes": "", "created_at": "2026-10-11T08:00:00Z",
            "parent_id": None, "backup_interval_days": 30, "target_location_ids": [], "backup_enabled": True, "private": False,
            **fields,
        }

    def payload(self, assets, schema=2, locations=None):
        return {
            "format": "filebackup-ledger", "schema_version": schema,
            "exported_at": "2026-10-11T08:00:00Z", "assets": [
                {key: value for key, value in asset.items() if key not in ("backup_target", "private")}
                if schema == 2 else asset for asset in assets],
            "versions": [], "locations": locations or [], "records": [],
        }

    @staticmethod
    def legacy(asset):
        return {key: value for key, value in asset.items()
                if key not in ("parent_id", "backup_interval_days", "target_location_ids", "backup_enabled", "backup_target", "private")}

    def test_backup_enabled_defaults_and_false_survive_edit_reopen_export(self):
        default = self.asset("默认继续备份", "file")
        self.assertIs(default.get("backup_enabled"), True)
        target = self.location()
        disabled = self.asset("不再备份", "file", backup_enabled=False, backup_interval_days=7,
                              target_location_ids=[target["id"]])
        self.assertIs(disabled["backup_enabled"], False)
        enabled = self.store.update("assets", disabled["id"], {"backup_enabled": True})
        edited = self.store.update("assets", enabled["id"], {"backup_enabled": False})
        self.assertEqual(edited, disabled)
        self.assertEqual(Store(self.db).state(), self.store.state())
        exported = self.store.export()
        self.assertEqual(exported["schema_version"], 3)
        self.assertIs(exported["assets"][1]["backup_enabled"], False)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT backup_enabled, typeof(backup_enabled) FROM assets WHERE id = ?",
                                          (disabled["id"],)).fetchone(), (0, "integer"))

    def test_backup_enabled_requires_exact_boolean_on_create_and_update(self):
        asset = self.asset("已启用", "file", backup_enabled=True)
        before = self.store.state()
        for value in (0, 1, -1, 0.0, 1.0, "true", "false", None, [], {}):
            with self.subTest(value=value):
                self.error(400, lambda v=value: self.asset("非法状态", "file", backup_enabled=v))
                self.error(400, lambda v=value: self.store.update("assets", asset["id"], {"backup_enabled": v}))
                self.assertEqual(self.store.state(), before)

    def test_schema1_and_earlier_schema2_default_enabled_but_schema2_false_is_preserved(self):
        legacy = self.imported_asset("格式1")
        earlier_v2 = self.imported_asset("早期格式2")
        del earlier_v2["backup_enabled"]
        disabled_v2 = self.imported_asset("格式2已关闭", backup_enabled=False)
        self.store.import_data(self.payload([self.legacy(legacy)], schema=1))
        self.store.import_data(self.payload([earlier_v2, disabled_v2]))
        indexed = {entry["id"]: entry for entry in self.store.state()["assets"]}
        self.assertIs(indexed[legacy["id"]].get("backup_enabled"), True)
        self.assertIs(indexed[earlier_v2["id"]].get("backup_enabled"), True)
        self.assertIs(indexed[disabled_v2["id"]].get("backup_enabled"), False)
        # Imports with a known ID retain its current switch and all other fields.
        self.store.import_data(self.payload([{**disabled_v2, "backup_enabled": True}]))
        self.assertEqual(self.store.state()["assets"], list(indexed.values()))
        schema1 = self.payload([{**self.legacy(legacy), "backup_enabled": True}], schema=1)
        self.error(400, lambda: self.store.import_data(schema1))

    def test_invalid_import_backup_enabled_rolls_back_new_entries_and_preserves_known(self):
        known = self.asset("当前关闭", "file", backup_enabled=False)
        good = self.imported_asset("有效新条目")
        before = self.store.state()
        for value in (0, 1, "false", None):
            with self.subTest(value=value):
                malformed = self.imported_asset("无效新条目", backup_enabled=value)
                self.error(400, lambda: self.store.import_data(self.payload([good, malformed])))
                self.error(400, lambda: self.store.import_data(self.payload([good, {**known, "backup_enabled": value}])))
                self.assertEqual(self.store.state(), before)

    def test_disabled_leaf_accepts_manual_records_without_inheriting_parent_setting(self):
        parent = self.asset("关闭的目录", backup_enabled=False)
        enabled_child = self.asset("默认启用的子项", "file", parent_id=parent["id"])
        disabled_child = self.asset("关闭的叶节点", "file", parent_id=parent["id"], backup_enabled=False, backup_target=True)
        self.assertIs(enabled_child["backup_enabled"], True)
        location = self.location()
        version = self.store.create("versions", {"asset_id": disabled_child["id"], "label": "manual"})
        payload = {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-10T10:00:00Z"}
        record = self.store.create("records", payload)
        another = self.store.create_batch([payload])[0]
        self.assertEqual(self.store.state()["records"], [record, another])
        parent_version = self.store.create("versions", {"asset_id": parent["id"], "label": "parent"})
        self.error(409, lambda: self.store.create("records", {**payload, "version_id": parent_version["id"]}))

    def test_create_defaults_are_typed_and_path_does_not_define_parent(self):
        root = self.asset("根目录", source_path=r"\\offline.invalid\share")
        with patch("os.scandir", side_effect=AssertionError("must never scan source paths")) as scan:
            child = self.asset("手动登记", "file", source_path=r"\\offline.invalid\share\document.pdf")
            scan.assert_not_called()
        self.assertEqual(child.get("parent_id", "missing"), None)
        self.assertEqual(child.get("backup_interval_days"), 30)
        self.assertEqual(child.get("target_location_ids"), [])
        self.assertEqual(Store(self.db).state(), self.store.state())
        self.assertEqual(Store(self.db).state()["assets"][1]["parent_id"], None)
        self.assertIsNone(root["parent_id"])

    def test_mixed_depth_tree_and_policy_survive_reopen_and_edits(self):
        target = self.location()
        root = self.asset("资料")
        shallow = self.asset("浅层备份文件", "file", parent_id=root["id"], backup_interval_days=1,
                             target_location_ids=[target["id"]])
        branch = self.asset("深入分支", parent_id=root["id"])
        deep = self.asset("深层备份文件夹", parent_id=branch["id"], backup_interval_days=3650)
        edited = self.store.update("assets", shallow["id"], {"parent_id": branch["id"], "backup_interval_days": 45})
        self.assertEqual(edited["created_at"], shallow["created_at"])
        self.assertEqual(edited["target_location_ids"], [target["id"]])
        self.assertEqual(deep["parent_id"], branch["id"])
        self.assertEqual(Store(self.db).state(), self.store.state())
        self.assertEqual(self.store.update("assets", edited["id"], {"parent_id": None})["parent_id"], None)

    def test_interval_rejects_bool_noninteger_and_out_of_bounds_atomically(self):
        current = self.asset("资料", "file")
        for value in (True, False, 0, -1, 3651, 30.0, "30", None):
            with self.subTest(value=value):
                snapshot = self.store.state()
                self.error(400, lambda v=value: self.asset("无效间隔", "file", backup_interval_days=v))
                self.error(400, lambda v=value: self.store.update("assets", current["id"], {"backup_interval_days": v}))
                self.assertEqual(self.store.state(), snapshot)

    def test_parent_must_be_a_folder_and_cannot_create_a_cycle(self):
        root = self.asset("根")
        child = self.asset("子", parent_id=root["id"])
        leaf = self.asset("文件", "file", parent_id=child["id"])
        snapshot = self.store.state()
        self.error(404, lambda: self.asset("孤立", parent_id=str(uuid.uuid4())))
        self.error(400, lambda: self.asset("文件下的子项", parent_id=leaf["id"]))
        self.error(400, lambda: self.store.update("assets", root["id"], {"parent_id": root["id"]}))
        self.error(400, lambda: self.store.update("assets", root["id"], {"parent_id": child["id"]}))
        self.error(400, lambda: self.store.update("assets", root["id"], {"parent_id": leaf["id"]}))
        for value in ("bad-id", 10, True, []):
            self.error(400, lambda v=value: self.asset("坏关联", parent_id=v))
        self.assertEqual(self.store.state(), snapshot)

    def test_nonempty_folder_cannot_be_deleted_or_turned_into_a_file(self):
        root = self.asset("根")
        child = self.asset("子", "file", parent_id=root["id"])
        self.error(409, lambda: self.store.delete("assets", root["id"]))
        self.error(409, lambda: self.store.update("assets", root["id"], {"kind": "file"}))
        self.store.delete("assets", child["id"])
        self.assertEqual(self.store.update("assets", root["id"], {"kind": "file"})["kind"], "file")
        removed = self.store.delete("assets", root["id"])
        self.assertTrue(removed['deleted'])
        self.assertIsInstance(removed['trash_id'], str)

    def test_target_locations_are_unique_valid_references_and_block_deletion(self):
        target = self.location()
        asset = self.asset("资料", "file", target_location_ids=[target["id"]])
        snapshot = self.store.state()
        for value in (None, target["id"], [True], ["not-uuid"], [target["id"], target["id"]]):
            with self.subTest(value=value):
                self.error(400, lambda v=value: self.store.update("assets", asset["id"], {"target_location_ids": v}))
        self.error(404, lambda: self.asset("未知目标", target_location_ids=[str(uuid.uuid4())]))
        self.error(409, lambda: self.store.delete("locations", target["id"]))
        self.assertEqual(self.store.state(), snapshot)
        self.store.update("assets", asset["id"], {"target_location_ids": []})
        self.store.delete("locations", target["id"])

    def test_intermediate_folder_history_is_preserved_when_child_is_added(self):
        root = self.asset("已有记录的根目录", backup_target=True)
        location = self.location()
        version = self.store.create("versions", {"asset_id": root["id"], "label": "历史v1"})
        record = self.store.create("records", {"version_id": version["id"], "location_id": location["id"],
                                               "backed_up_at": "2026-10-10T10:00:00Z"})
        self.asset("后来登记的子项", "file", parent_id=root["id"])
        self.assertEqual(self.store.state()["versions"], [version])
        self.assertEqual(self.store.state()["records"], [record])
        self.assertEqual(Store(self.db).state(), self.store.state())

    def test_new_records_recheck_leaf_after_child_is_registered_but_history_remains_editable(self):
        folder = self.asset("曾经是叶节点", backup_target=True)
        location = self.location()
        version = self.store.create("versions", {"asset_id": folder["id"], "label": "v1"})
        payload = {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-10T10:00:00Z"}
        historical = self.store.create("records", payload)
        self.asset("后来添加的子项", "file", parent_id=folder["id"])
        before = self.store.state()
        self.error(409, lambda: self.store.create("records", payload))
        self.assertEqual(self.store.state(), before)
        edited = self.store.update("records", historical["id"], {"notes": "保留中间目录的历史"})
        self.assertEqual(edited["id"], historical["id"])
        self.assertEqual(edited["notes"], "保留中间目录的历史")

    def test_batch_record_leaf_check_rolls_back_an_earlier_valid_leaf_record(self):
        folder = self.asset("中间目录")
        child = self.asset("叶节点文件", "file", parent_id=folder["id"], backup_target=True)
        location = self.location()
        versions = [self.store.create("versions", {"asset_id": asset["id"], "label": "v1"}) for asset in (child, folder)]
        records = [{"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-10T10:00:00Z"} for version in versions]
        before = self.store.state()
        self.error(409, lambda: self.store.create_batch(records))
        self.assertEqual(self.store.state(), before)

    def test_import_keeps_historical_records_for_intermediate_folders(self):
        parent = self.imported_asset("历史中间目录")
        child = self.imported_asset("子文件", "file", parent_id=parent["id"])
        payload = self.payload([child, parent])
        location = {"id": str(uuid.uuid4()), "name": "历史位置", "kind": "other", "base_path": "", "notes": "",
                    "created_at": "2026-10-11T08:00:00Z"}
        version = {"id": str(uuid.uuid4()), "asset_id": parent["id"], "label": "历史v1", "notes": "",
                   "created_at": "2026-10-11T08:00:00Z"}
        record = {"id": str(uuid.uuid4()), "version_id": version["id"], "location_id": location["id"],
                  "backed_up_at": "2026-10-10T10:00:00Z", "stored_path": "", "notes": "", "created_at": "2026-10-11T08:00:00Z"}
        payload.update(locations=[location], versions=[version], records=[record])
        self.store.import_data(payload)
        self.assertEqual(self.store.state()["records"], [record])

    def test_import_v2_requires_hierarchy_fields_and_child_before_parent_import_is_valid(self):
        target = self.location()
        parent = self.imported_asset("父目录")
        child = self.imported_asset("先排列的子文件", "file", parent_id=parent["id"],
                                    backup_interval_days=7, target_location_ids=[target["id"]])
        payload = self.payload([child, parent], locations=[target])
        destination = Store(Path(self.temp.name) / "import.sqlite3")
        self.assertEqual(destination.import_data(payload)["added"]["assets"], 2)
        exported = destination.export()
        self.assertEqual(exported["schema_version"], 3)
        self.assertEqual(exported["assets"], [{**child, "backup_target": False}, {**parent, "backup_target": False}])
        for field in ("parent_id", "backup_interval_days", "target_location_ids"):
            broken = copy.deepcopy(payload)
            del broken["assets"][0][field]
            self.error(400, lambda b=broken: self.store.import_data(b))

    def test_import_v2_invalid_graph_and_policies_roll_back_every_collection(self):
        parent = self.imported_asset("父目录")
        child = self.imported_asset("子文件", "file", parent_id=parent["id"])
        good = self.payload([child, parent])
        good["locations"] = [{"id": str(uuid.uuid4()), "name": "导入位置", "kind": "local", "base_path": "",
                              "notes": "", "created_at": "2026-10-11T08:00:00Z"}]
        good["versions"] = [{"id": str(uuid.uuid4()), "asset_id": child["id"], "label": "v1", "notes": "",
                             "created_at": "2026-10-11T08:00:00Z"}]
        good["records"] = [{"id": str(uuid.uuid4()), "version_id": good["versions"][0]["id"],
                            "location_id": good["locations"][0]["id"], "backed_up_at": "2026-10-10T10:00:00Z",
                            "stored_path": "", "notes": "", "created_at": "2026-10-11T08:00:00Z"}]
        for fault in ("missing_parent", "file_parent", "self", "cycle", "missing_target", "invalid_target",
                      "duplicate_target", "bool_interval", "too_large_interval"):
            with self.subTest(fault=fault):
                broken = copy.deepcopy(good)
                first, second = broken["assets"]
                if fault == "missing_parent":
                    first["parent_id"] = str(uuid.uuid4())
                elif fault == "file_parent":
                    second["kind"] = "file"
                elif fault == "self":
                    first["parent_id"] = first["id"]
                elif fault == "cycle":
                    first["kind"] = "folder"
                    second["parent_id"] = first["id"]
                elif fault == "missing_target":
                    first["target_location_ids"] = [str(uuid.uuid4())]
                elif fault == "invalid_target":
                    first["target_location_ids"] = [False]
                elif fault == "duplicate_target":
                    first["target_location_ids"] = [good["locations"][0]["id"]] * 2
                elif fault == "bool_interval":
                    first["backup_interval_days"] = True
                elif fault == "too_large_interval":
                    first["backup_interval_days"] = 3651
                before = self.store.state()
                expected = 400 if fault in ("invalid_target", "duplicate_target", "bool_interval", "too_large_interval") else 409
                self.error(expected, lambda b=broken: self.store.import_data(b))
                self.assertEqual(self.store.state(), before)

    def test_import_validates_known_references_without_overwriting_known_fields(self):
        root = self.asset("保留当前父目录")
        target = self.location()
        known = self.store.export()["assets"][0]
        altered = {**known, "name": "导入名称", "backup_interval_days": 60, "target_location_ids": [target["id"]]}
        child = self.imported_asset("新子目录", parent_id=root["id"])
        self.store.import_data(self.payload([child, altered]))
        self.assertEqual(self.store.state()["assets"][1], known)
        snapshot = self.store.state()
        for field, value in (("parent_id", str(uuid.uuid4())), ("target_location_ids", [str(uuid.uuid4())])):
            self.error(409, lambda f=field, v=value: self.store.import_data(self.payload([{**known, f: v}])))
            self.assertEqual(self.store.state(), snapshot)

    def test_import_effective_graph_uses_current_known_entries(self):
        root = self.asset("当前文件", "file")
        known = self.store.export()["assets"][0]
        child = self.imported_asset("无法挂在文件下面", parent_id=root["id"])
        before = self.store.state()
        self.error(409, lambda: self.store.import_data(self.payload([child, {**known, "kind": "folder"}])))
        self.assertEqual(self.store.state(), before)

    def test_v1_import_infers_only_new_entries_using_current_stored_paths(self):
        root = self.asset("已有根目录", source_path="D:/docs")
        existing = self.asset("手动根层文件", "file", source_path="D:/docs/manual.txt")
        known = self.legacy(self.store.export()["assets"][0])
        known["source_path"] = "Z:/ignored"
        branch = self.imported_asset("新分支", source_path=r"d:\DOCS\branch")
        child = self.imported_asset("新深层文件", "file", source_path="D:/docs/branch/report.pdf")
        payload = self.payload([self.legacy(child), self.legacy(branch), known], schema=1)
        self.store.import_data(payload)
        indexed = {entry["id"]: entry for entry in self.store.state()["assets"]}
        self.assertEqual(indexed[branch["id"]]["parent_id"], root["id"])
        self.assertEqual(indexed[child["id"]]["parent_id"], branch["id"])
        self.assertEqual(indexed[child["id"]]["backup_interval_days"], 30)
        self.assertEqual(indexed[child["id"]]["target_location_ids"], [])
        self.assertIsNone(indexed[existing["id"]]["parent_id"])
        self.assertEqual(indexed[root["id"]], root)
        self.assertEqual(Store(self.db).state(), self.store.state())

    def test_v1_import_is_strict_and_rejects_global_id_collisions(self):
        asset = self.imported_asset("旧格式资料", "file")
        good = self.payload([self.legacy(asset)], schema=1)
        for field, value in (("parent_id", None), ("backup_interval_days", 30), ("target_location_ids", []), ("backup_enabled", True), ("extra", "x")):
            broken = copy.deepcopy(good)
            broken["assets"][0][field] = value
            self.error(400, lambda b=broken: self.store.import_data(b))
        self.store.import_data(good)
        location = self.location()
        collision = self.payload([self.imported_asset("冲突", id=location["id"])])
        before = self.store.state()
        self.error(409, lambda: self.store.import_data(collision))
        self.assertEqual(self.store.state(), before)


class MigrationTests(unittest.TestCase):
    def test_old_database_infers_nearest_unambiguous_folder_once_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "legacy.sqlite3"
            entries = [HierarchyTests.imported_asset(name, kind, source_path=path) for name, kind, path in (
                ("根目录", "folder", "D:/Docs"),
                ("子目录", "folder", "d:\\DOCS\\project\\"),
                ("深层文件", "file", "D:/docs/project/paper.pdf"),
                ("浅层文件", "file", "D:/docs/overview.pdf"),
                ("相似前缀", "file", "D:/docs-old/file.pdf"),
                ("重复目录1", "folder", "D:/Docs/duplicate"),
                ("重复目录2", "folder", r"d:\docs\DUPLICATE"),
                ("歧义子项", "file", "D:/docs/duplicate/file.pdf"),
                ("无路径", "folder", ""),
                ("同路径", "file", "D:/docs"),
                ("UNC根", "folder", r"\\offline.invalid\share"),
                ("UNC子项", "file", "//OFFLINE.INVALID/share/report.pdf"),
                ("相对路径", "file", "offline.invalid/share/report.pdf"),
            )]
            version = {"id": str(uuid.uuid4()), "asset_id": entries[0]["id"], "label": "historical", "notes": "原有版本",
                       "created_at": "2026-10-09T08:00:00Z"}
            target = {"id": str(uuid.uuid4()), "name": "历史位置", "kind": "other", "base_path": "", "notes": "原有位置",
                      "created_at": "2026-10-09T08:00:00Z"}
            record = {"id": str(uuid.uuid4()), "version_id": version["id"], "location_id": target["id"],
                      "backed_up_at": "2026-10-10T10:00:00Z", "stored_path": "offline-copy", "notes": "原有备份事实",
                      "created_at": "2026-10-10T08:00:00Z"}
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript("""
                    CREATE TABLE assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, source_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE versions (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, label TEXT NOT NULL, label_key TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(asset_id, label_key));
                    CREATE TABLE locations (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, base_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE records (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), location_id TEXT NOT NULL REFERENCES locations(id), backed_up_at TEXT NOT NULL, stored_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
                """)
                conn.executemany("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)", [
                    tuple(entry[field] for field in ("id", "name", "kind", "source_path", "notes", "created_at")) for entry in entries])
                conn.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)",
                             (version["id"], version["asset_id"], version["label"], version["label"].casefold(), version["notes"], version["created_at"]))
                conn.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (target["id"], target["name"], target["name"].casefold(), target["kind"], target["base_path"], target["notes"], target["created_at"]))
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(record.values()))
            with patch("os.scandir", side_effect=AssertionError("migration must not scan paths")) as scan:
                store = Store(db)
                scan.assert_not_called()
            state = store.state()
            self.assertTrue(all(entry.get("backup_enabled") is True for entry in state["assets"]))
            indexed = {entry["name"]: entry for entry in state["assets"]}
            for name, parent in (("子目录", "根目录"), ("深层文件", "子目录"), ("浅层文件", "根目录"), ("UNC子项", "UNC根")):
                self.assertEqual(indexed[name].get("parent_id"), indexed[parent]["id"])
            for name in ("根目录", "相似前缀", "歧义子项", "无路径", "同路径", "UNC根", "相对路径"):
                self.assertIsNone(indexed[name]["parent_id"])
            self.assertEqual([HierarchyTests.legacy(entry) for entry in state["assets"]],
                             [HierarchyTests.legacy(entry) for entry in entries])
            self.assertEqual(state["versions"], [version])
            self.assertEqual(state["locations"], [target])
            self.assertEqual(state["records"], [record])
            # A user detaches an inferred child. Reopening must never infer it again.
            store.update("assets", indexed["浅层文件"]["id"], {"parent_id": None})
            before = store.state()
            self.assertEqual(Store(db).state(), before)
            self.assertEqual(Store(db).state()["versions"], [version])
            self.assertEqual(Store(db).state()["records"], [record])
            with closing(sqlite3.connect(db)) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(assets)")}
                self.assertTrue({"parent_id", "backup_interval_days", "target_location_ids", "backup_enabled"}.issubset(columns))

    def test_hierarchy_database_adds_enabled_without_reinferring_parents_or_changing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "prior-hierarchy.sqlite3"
            parent = HierarchyTests.imported_asset("已有根目录", source_path="D:/docs", backup_interval_days=14)
            # The path is nested, but the manually detached child must stay at root.
            child = HierarchyTests.imported_asset("手动根层文件", "file", source_path="D:/docs/file.pdf")
            version = {"id": str(uuid.uuid4()), "asset_id": child["id"], "label": "v1", "notes": "历史版本",
                       "created_at": "2026-10-09T08:00:00Z"}
            target = {"id": str(uuid.uuid4()), "name": "旧位置", "kind": "local", "base_path": "", "notes": "",
                      "created_at": "2026-10-09T08:00:00Z"}
            child["target_location_ids"] = [target["id"]]
            record = {"id": str(uuid.uuid4()), "version_id": version["id"], "location_id": target["id"],
                      "backed_up_at": "2026-10-10T10:00:00Z", "stored_path": "stored", "notes": "历史备份",
                      "created_at": "2026-10-10T08:00:00Z"}
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript("""
                    CREATE TABLE assets (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, source_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL, parent_id TEXT REFERENCES assets(id) DEFERRABLE INITIALLY DEFERRED, backup_interval_days INTEGER NOT NULL DEFAULT 30, target_location_ids TEXT NOT NULL DEFAULT '[]');
                    CREATE TABLE versions (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE, label TEXT NOT NULL, label_key TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(asset_id, label_key));
                    CREATE TABLE locations (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, base_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE records (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), location_id TEXT NOT NULL REFERENCES locations(id), backed_up_at TEXT NOT NULL, stored_path TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
                """)
                for asset in (parent, child):
                    conn.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (*(asset[field] for field in ("id", "name", "kind", "source_path", "notes", "created_at", "parent_id", "backup_interval_days")),
                                  json.dumps(asset["target_location_ids"])))
                conn.execute("INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?)",
                             (version["id"], version["asset_id"], version["label"], version["label"].casefold(), version["notes"], version["created_at"]))
                conn.execute("INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (target["id"], target["name"], target["name"].casefold(), target["kind"], target["base_path"], target["notes"], target["created_at"]))
                conn.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(record.values()))
            expected = {"assets": [{**parent, "backup_target": False}, {**child, "backup_target": True}],
                        "versions": [version], "locations": [target], "records": [record]}
            store = Store(db)
            self.assertEqual(store.state(), expected)
            self.assertEqual(Store(db).state(), expected)


if __name__ == "__main__":
    unittest.main()
