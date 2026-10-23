"""Atomic migration against disposable databases on the isolated replica set."""

import copy
import importlib
import inspect
import io
import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pymongo import MongoClient
from backend.mongo_store import MongoStore
from backend.store import COLLECTIONS, StoreError, validate_entry


ROOT = Path(__file__).resolve().parents[1]
URI = os.environ.get("DATABACK_TEST_MONGO_URI")


def snapshot():
    time = "2026-10-14T07:00:00.123456Z"
    asset = validate_entry("assets", {"name": "原始资料", "kind": "file", "source_path": r"\\offline.invalid\资料",
                                     "backup_target": True, "backup_enabled": False})
    asset.update(id=str(uuid.uuid4()), created_at=time)
    return {"format": "filebackup-ledger", "schema_version": 3, "exported_at": time,
            "assets": [asset], "versions": [], "locations": [], "records": []}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        if not URI:
            self.skipTest("DATABACK_TEST_MONGO_URI is not configured")
        self.database_name = "databack_test_migration_" + uuid.uuid4().hex
        self.client = MongoClient(URI)
        self.addCleanup(self.cleanup)
        self.store = None

    def cleanup(self):
        self.client.drop_database(self.database_name)
        if self.store is not None:
            self.store.close()
        self.client.close()

    def adapter(self):
        self.assertIn("require_empty", inspect.signature(MongoStore.import_data).parameters,
                      "atomic empty-ledger migration guard is missing")
        if self.store is None:
            self.store = MongoStore(URI, self.database_name)
        return self.store

    def migration(self):
        self.assertTrue((ROOT / "scripts/migrate_to_mongo.py").is_file(), "migration CLI is missing")
        return importlib.import_module("scripts.migrate_to_mongo")

    def test_nonempty_refusal_does_not_change_public_data_or_guard(self):
        store = self.adapter()
        store.create("locations", {"name": "existing", "kind": "nas"})
        before = store.state()
        guard = store.database["_meta"].find_one({"_id": "mutation"})
        with self.assertRaises(StoreError) as caught:
            store.import_data(snapshot(), require_empty=True)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(store.state(), before)
        self.assertEqual(store.database["_meta"].find_one({"_id": "mutation"}), guard)

    def test_empty_import_ignores_metadata_and_preserves_raw_fields_and_times(self):
        store = self.adapter()
        payload = snapshot()
        payload["assets"][0]["created_at"] = "2026-10-14T15:00:00.123456+08:00"
        result = store.import_data(payload, require_empty=True)
        self.assertEqual(result["added"], {"assets": 1, "versions": 0, "locations": 0, "records": 0})
        self.assertEqual(store.state(), {key: payload[key] for key in COLLECTIONS})

    def test_old_schema3_migration_defaults_optional_private_flag(self):
        store = self.adapter()
        payload = snapshot()
        payload['assets'][0].pop('private')
        result = store.import_data(payload, require_empty=True)
        self.assertEqual(result['added']['assets'], 1)
        self.assertIs(store.state()['assets'][0]['private'], False)
        self.assertEqual({key: value for key, value in store.state()['assets'][0].items() if key != 'private'}, payload['assets'][0])

    def test_concurrent_empty_imports_only_one_commits(self):
        store = self.adapter()
        other = MongoStore(URI, self.database_name)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)
        payloads = [snapshot(), snapshot()]
        def run(index):
            barrier.wait(timeout=5)
            try:
                (store, other)[index].import_data(payloads[index], require_empty=True)
                return index
            except StoreError as error:
                self.assertEqual(error.status, 409)
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        winners = [index for index in results if index is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(store.state(), {key: payloads[winners[0]][key] for key in COLLECTIONS})

    def test_interruption_after_first_insert_rolls_back_everything(self):
        store = self.adapter()
        payload = snapshot()
        payload["assets"].append({**payload["assets"][0], "id": str(uuid.uuid4()), "name": "second"})
        insert = store._insert
        count = 0
        def interrupt(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise KeyboardInterrupt()
            return insert(*args)
        before = store.database["_meta"].find_one({"_id": "mutation"})
        with patch.object(store, "_insert", interrupt), self.assertRaises(KeyboardInterrupt):
            store.import_data(payload, require_empty=True)
        self.assertEqual(store.state(), {key: [] for key in COLLECTIONS})
        self.assertEqual(store.database["_meta"].find_one({"_id": "mutation"}), before)

    def test_import_order_mismatch_aborts_before_commit(self):
        store = self.adapter()
        payload = snapshot()
        payload["assets"].append({**payload["assets"][0], "id": str(uuid.uuid4()), "created_at": "2020-01-01T00:00:00Z"})
        with self.assertRaises(StoreError):
            store.import_data(payload, require_empty=True)
        self.assertEqual(store.state(), {key: [] for key in COLLECTIONS})

    def test_validation_failure_leaves_previous_data_unchanged(self):
        store = self.adapter()
        payload = snapshot()
        payload["assets"][0]["parent_id"] = str(uuid.uuid4())
        with self.assertRaises(StoreError):
            store.import_data(payload, require_empty=True)
        self.assertEqual(store.state(), {key: [] for key in COLLECTIONS})

    def test_dry_run_does_not_create_collections_or_metadata_then_apply_is_exact(self):
        migration = self.migration()
        payload = snapshot()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            receipt = Path(folder) / "receipt.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            env = {"MONGODB_URI": URI, "MONGODB_DATABASE": self.database_name}
            with patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
                self.assertEqual(migration.main([str(path), "--dry-run", "--receipt", str(receipt)]), 0)
                self.assertEqual(self.client[self.database_name].list_collection_names(), [])
                self.assertTrue(receipt.is_file())
                self.assertEqual(migration.main([str(path), "--apply", "--receipt", str(receipt)]), 0)
            self.store = MongoStore(URI, self.database_name)
            self.assertEqual(self.store.state(), {key: payload[key] for key in COLLECTIONS})
            self.assertEqual(migration.canonical_summary(self.store.state()), migration.canonical_summary(payload))

    def test_apply_requires_matching_prior_dry_run_receipt(self):
        migration = self.migration()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            env = {"MONGODB_URI": URI, "MONGODB_DATABASE": self.database_name}
            with patch.dict(os.environ, env), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(migration.main([str(path), "--apply"]), 2)
                self.assertEqual(self.client[self.database_name].list_collection_names(), [])
                self.assertEqual(migration.main([str(path), "--dry-run"]), 0)
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["assets"][0]["notes"] = "changed after dry run"
                path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(migration.main([str(path), "--apply"]), 2)
                self.assertEqual(self.client[self.database_name].list_collection_names(), [])

    def test_cli_invalid_snapshot_or_uri_prints_sanitized_failure(self):
        migration = self.migration()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text(json.dumps(snapshot()), encoding="utf-8")
            output = io.StringIO()
            env = {"MONGODB_URI": "mongodb://private-user:private-password@private-host:bad-port/",
                   "MONGODB_DATABASE": self.database_name}
            with patch.dict(os.environ, env), redirect_stderr(output), redirect_stdout(output):
                self.assertEqual(migration.main([str(path), "--dry-run"]), 2)
            self.assertNotIn("private", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())

    def test_snapshot_rejects_duplicate_json_keys_and_broken_references(self):
        migration = self.migration()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            path.write_text('{"format":"filebackup-ledger","format":"filebackup-ledger"}', encoding="utf-8")
            with self.assertRaises(migration.MigrationError):
                migration.load_snapshot(path)
            payload = snapshot()
            payload["assets"][0]["target_location_ids"] = [str(uuid.uuid4())]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(migration.MigrationError):
                migration.load_snapshot(path)


if __name__ == "__main__":
    unittest.main()

