import copy
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.store import Store, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "ledger.sqlite3"
        self.store = Store(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self):
        asset = self.store.create("assets", {"name": "项目资料", "kind": "folder", "source_path": "D:/项目", "backup_target": True})
        version = self.store.create("versions", {"asset_id": asset["id"], "label": "v1.0"})
        disk = self.store.create("locations", {"name": "移动硬盘", "kind": "external", "base_path": "E:/"})
        cloud = self.store.create("locations", {"name": "网盘", "kind": "cloud"})
        return asset, version, disk, cloud

    def record(self, version, location, when="2026-10-08T20:30:00-07:00", **extra):
        return {"version_id": version["id"], "location_id": location["id"], "backed_up_at": when, **extra}

    def assert_error(self, status, callback):
        with self.assertRaises(StoreError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        self.assertTrue(str(caught.exception))

    def test_same_version_keeps_distinct_location_times_and_repeat_history(self):
        _, version, disk, cloud = self.fixture()
        first = self.store.create("records", self.record(version, disk))
        second = self.store.create("records", self.record(version, cloud, "2026-10-10T09:15:00-07:00"))
        repeated = self.store.create("records", self.record(version, disk))
        records = self.store.state()["records"]
        self.assertEqual(len(records), 3)
        self.assertEqual(first["backed_up_at"], "2026-10-09T03:30:00Z")
        self.assertEqual(second["backed_up_at"], "2026-10-10T16:15:00Z")
        self.assertNotEqual(first["id"], repeated["id"])
        self.assertEqual(first["stored_path"], "")

    def test_old_version_late_backup_does_not_change_version_order(self):
        asset, old, disk, _ = self.fixture()
        latest = self.store.create("versions", {"asset_id": asset["id"], "label": "v2.0"})
        self.store.create("records", self.record(old, disk, "2027-01-01T00:00:00Z"))
        versions = self.store.state()["versions"]
        self.assertEqual([v["id"] for v in versions], [old["id"], latest["id"]])
        self.assertEqual(versions[0]["created_at"], old["created_at"])

    def test_reopen_preserves_all_data_and_empty_database_has_no_seed(self):
        self.assertEqual(self.store.state(), {"assets": [], "versions": [], "locations": [], "records": []})
        _, version, disk, _ = self.fixture()
        self.store.create("records", self.record(version, disk))
        self.assertEqual(Store(self.db).state(), self.store.state())

    def test_update_preserves_identity_and_created_at_and_relations(self):
        asset, version, disk, cloud = self.fixture()
        record = self.store.create("records", self.record(version, disk))
        edited = self.store.update("records", record["id"], {"location_id": cloud["id"], "notes": "已核对"})
        self.assertEqual(edited["id"], record["id"])
        self.assertEqual(edited["created_at"], record["created_at"])
        self.assertEqual(edited["version_id"], version["id"])
        self.assertEqual(edited["location_id"], cloud["id"])
        renamed = self.store.update("assets", asset["id"], {"name": "新名称"})
        self.assertEqual(renamed["source_path"], asset["source_path"])
        self.assert_error(400, lambda: self.store.update("records", record["id"], {"id": str(uuid.uuid4())}))
        self.assert_error(404, lambda: self.store.update("records", record["id"], {"version_id": str(uuid.uuid4())}))
        self.assertEqual(self.store.state()["records"][0], edited)

    def test_deletion_guards_and_empty_asset_cascade(self):
        asset, version, disk, _ = self.fixture()
        record = self.store.create("records", self.record(version, disk))
        for collection, item in (("assets", asset), ("versions", version), ("locations", disk)):
            self.assert_error(409, lambda c=collection, i=item: self.store.delete(c, i["id"]))
        self.store.delete("records", record["id"])
        self.store.delete("assets", asset["id"])
        self.assertEqual(self.store.state()["versions"], [])
        self.store.delete("locations", disk["id"])
        self.assert_error(404, lambda: self.store.delete("assets", asset["id"]))

    def test_batch_is_atomic_and_allows_independent_times(self):
        _, version, disk, cloud = self.fixture()
        created = self.store.create_batch([self.record(version, disk), self.record(version, cloud, "2026-10-10T09:15:00-07:00")])
        self.assertEqual(len(created), 2)
        snapshot = self.store.state()
        bad = self.record(version, cloud, "2026-10-10T09:15:00")
        self.assert_error(400, lambda: self.store.create_batch([self.record(version, disk), bad]))
        self.assertEqual(self.store.state(), snapshot)
        self.assert_error(400, lambda: self.store.create_batch([]))

    def test_input_validation_and_case_insensitive_uniqueness(self):
        asset, version, disk, _ = self.fixture()
        self.assert_error(409, lambda: self.store.create("versions", {"asset_id": asset["id"], "label": "V1.0"}))
        self.assert_error(409, lambda: self.store.create("locations", {"name": disk["name"], "kind": "local"}))
        other = self.store.create("locations", {"name": "Straße", "kind": "other"})
        self.assert_error(409, lambda: self.store.create("locations", {"name": "STRASSE", "kind": "other"}))
        for payload in ({"name": "", "kind": "file"}, {"name": 12, "kind": "file"}, {"name": "资料", "kind": "bad"}, {"name": "资料", "kind": "file", "unexpected": True}, {"name": "资料", "kind": "file", "notes": None}):
            self.assert_error(400, lambda p=payload: self.store.create("assets", p))
        for when in ("2026-10-08T20:30:00", "not a date", "2026-02-31T20:30:00Z"):
            self.assert_error(400, lambda t=when: self.store.create("records", self.record(version, other, t)))
        self.assert_error(404, lambda: self.store.create("versions", {"asset_id": str(uuid.uuid4()), "label": "v1"}))

    def test_export_import_adds_unknown_ids_preserving_current_entries(self):
        asset, version, disk, _ = self.fixture()
        self.store.create("records", self.record(version, disk))
        exported = self.store.export()
        self.assertEqual(exported["format"], "filebackup-ledger")
        self.assertEqual(exported["schema_version"], 3)
        target = Store(Path(self.temp.name) / "second.sqlite3")
        self.assertEqual(target.import_data(exported), {"added": {"assets": 1, "versions": 1, "locations": 2, "records": 1}})
        self.assertEqual(target.state(), self.store.state())
        target.update("assets", asset["id"], {"name": "保留当前修改"})
        self.assertEqual(target.import_data(exported), {"added": {"assets": 0, "versions": 0, "locations": 0, "records": 0}})
        self.assertEqual(target.state()["assets"][0]["name"], "保留当前修改")

    def test_bad_import_rolls_back_all_entries(self):
        _, version, disk, _ = self.fixture()
        self.store.create("records", self.record(version, disk))
        exported = self.store.export()
        target = Store(Path(self.temp.name) / "target.sqlite3")
        for change in ("relation", "duplicate", "name_conflict", "version_conflict", "time", "field", "format"):
            broken = copy.deepcopy(exported)
            if change == "relation":
                broken["records"][0]["location_id"] = str(uuid.uuid4())
            elif change == "duplicate":
                broken["assets"].append(copy.deepcopy(broken["assets"][0]))
            elif change == "name_conflict":
                extra = copy.deepcopy(broken["locations"][0]); extra["id"] = str(uuid.uuid4()); broken["locations"].append(extra)
            elif change == "version_conflict":
                extra = copy.deepcopy(broken["versions"][0]); extra["id"] = str(uuid.uuid4()); broken["versions"].append(extra)
            elif change == "time":
                broken["records"][0]["backed_up_at"] = "2026-10-08T20:30:00"
            elif change == "field":
                broken["assets"][0]["hidden"] = True
            elif change == "format":
                broken["schema_version"] = 4
            self.assert_error(400 if change in ("duplicate", "time", "field", "format") else 409, lambda b=broken: target.import_data(b))
            self.assertEqual(target.state(), {"assets": [], "versions": [], "locations": [], "records": []})

    def test_import_conflict_with_current_location_rolls_back_new_asset(self):
        self.fixture()
        payload = self.store.export()
        payload["assets"][0]["id"] = str(uuid.uuid4())
        payload["versions"][0]["asset_id"] = payload["assets"][0]["id"]
        payload["versions"][0]["id"] = str(uuid.uuid4())
        payload["locations"][0]["id"] = str(uuid.uuid4())
        snapshot = self.store.state()
        self.assert_error(409, lambda: self.store.import_data(payload))
        self.assertEqual(self.store.state(), snapshot)

    def test_state_is_one_consistent_snapshot_during_concurrent_writes(self):
        self.fixture()
        original = self.store._transaction
        reached_versions, wrote, result = threading.Event(), threading.Event(), {}
        @contextmanager
        def gated_transaction():
            with original() as connection:
                def trace(sql):
                    if sql.startswith("SELECT * FROM versions"):
                        reached_versions.set()
                        # Give a concurrent writer a chance to expose an inconsistent
                        # read if state() fails to hold a snapshot across its queries.
                        wrote.wait(timeout=0.15)
                connection.set_trace_callback(trace)
                yield connection
        self.store._transaction = gated_transaction
        reader = threading.Thread(target=lambda: result.update(self.store.state()))
        other = Store(self.db)
        def write():
            new_asset = other.create("assets", {"name": "并发资料", "kind": "file"})
            other.create("versions", {"asset_id": new_asset["id"], "label": "并发版本"})
            wrote.set()
        reader.start()
        self.assertTrue(reached_versions.wait(timeout=2))
        writer = threading.Thread(target=write)
        writer.start()
        reader.join(timeout=2)
        writer.join(timeout=2)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        asset_ids = {asset["id"] for asset in result["assets"]}
        self.assertTrue(all(version["asset_id"] in asset_ids for version in result["versions"]))

    def test_imported_creation_order_handles_whole_seconds_and_microseconds(self):
        payload = self.store.export()
        payload["schema_version"] = 2
        chronological = [{"id": str(uuid.uuid4()), "name": name, "kind": "file", "source_path": "", "notes": "",
                          "parent_id": None, "backup_interval_days": 30, "target_location_ids": [], "created_at": created}
                         for name, created in (("整秒", "2026-10-11T08:00:00Z"), ("一微秒", "2026-10-11T08:00:00.000001Z"), ("两微秒", "2026-10-11T08:00:00.000002Z"))]
        payload["assets"] = list(reversed(chronological))
        self.store.import_data(payload)
        self.assertEqual([asset["id"] for asset in self.store.state()["assets"]], [asset["id"] for asset in chronological])


if __name__ == "__main__":
    unittest.main()
