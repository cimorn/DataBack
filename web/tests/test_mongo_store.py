"""Real Mongo contracts. Set DATABACK_TEST_MONGO_URI to an isolated replica set.

Every test owns databases beginning ``databack_test_``; the URI's database is
never selected. The suite never connects to the deployed ``databack`` database.
Run: python -m unittest discover -s tests -p test_mongo_store.py -v
"""

import importlib.util
import json
import os
import secrets
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from unittest.mock import patch

import test_backup_target as target_contract
import test_hierarchy as hierarchy_contract
import test_store as store_contract
import test_order as order_contract
from backend.store import COLLECTIONS, FIELDS, Store, StoreError


TEST_URI = os.environ.get("DATABACK_TEST_MONGO_URI")


class StorageFactoryTests(unittest.TestCase):
    def test_native_mongo_adapter_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.mongo_store"), "native Mongo adapter is missing")

    def test_factory_exists_and_preserves_sqlite_default(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.storage"), "storage factory is missing")
        from backend.storage import create_store
        with tempfile.TemporaryDirectory() as folder:
            store = create_store(Path(folder) / "ledger.sqlite3")
            self.assertIsInstance(store, Store)
            self.assertEqual(store.state(), {collection: [] for collection in COLLECTIONS})
            self.assertTrue(store.readiness())
            store.close()

    def test_invalid_mongo_uri_is_sanitized_without_sqlite_fallback(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.storage"), "storage factory is missing")
        from backend.storage import create_store
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "must-not-exist.sqlite3"
            with self.assertRaises(StoreError) as caught:
                create_store(path, mongo_uri="mongodb://private-user:secret-password@private-host:bad-port/")
            self.assertEqual(caught.exception.status, 503)
            for secret in ("private-user", "secret-password", "private-host", "bad-port"):
                self.assertNotIn(secret, str(caught.exception))
            self.assertFalse(path.exists())


class MongoFixture:
    """Run the public SQLite contract on native Mongo, including reopen/import."""

    def setUp(self):
        if not TEST_URI:
            self.skipTest("DATABACK_TEST_MONGO_URI is not configured")
        self.mongo_stores = []
        self.mongo_databases = {}
        self.database_prefix = "databack_test_" + uuid.uuid4().hex
        self.store_patch = patch(self.contract_module.__name__ + ".Store", self.mongo_factory)
        self.store_patch.start()
        self.addCleanup(self.cleanup_mongo)
        super().setUp()

    def mongo_factory(self, path=None):
        from backend.storage import create_store
        key = str(path)
        database = self.mongo_databases.setdefault(key, self.database_prefix + "_" + str(len(self.mongo_databases)))
        store = create_store(mongo_uri=TEST_URI, mongo_database=database)
        # Existing parity tests use this SQLite-only attribute as a reopen key.
        # Keep that compatibility in the test adapter rather than production.
        store.db_path = path
        self.mongo_stores.append(store)
        return store

    def cleanup_mongo(self):
        self.store_patch.stop()
        if self.mongo_stores:
            client = self.mongo_stores[0].client
            for database in self.mongo_databases.values():
                client.drop_database(database)
        for store in self.mongo_stores:
            store.close()


class MongoStoreContract(MongoFixture, store_contract.StoreTests):
    contract_module = store_contract

    def test_state_is_one_consistent_snapshot_during_concurrent_writes(self):
        self.fixture()
        original = self.store._read_collection
        other = self.mongo_factory(self.db)
        wrote = False
        def gated(collection, session):
            nonlocal wrote
            if collection == "versions" and not wrote:
                asset = other.create("assets", {"name": "concurrent", "kind": "file"})
                other.create("versions", {"asset_id": asset["id"], "label": "concurrent"})
                wrote = True
            return original(collection, session)
        with patch.object(self.store, "_read_collection", gated):
            state = self.store.state()
        self.assertTrue(wrote)
        asset_ids = {entry["id"] for entry in state["assets"]}
        self.assertTrue(all(entry["asset_id"] in asset_ids for entry in state["versions"]))
        self.assertEqual(len(state["versions"]), 1)
        self.assertEqual(len(other.state()["versions"]), 2)


class MongoHierarchyContract(MongoFixture, hierarchy_contract.HierarchyTests):
    contract_module = hierarchy_contract

    def test_backup_enabled_defaults_and_false_survive_edit_reopen_export(self):
        self.assertIs(self.asset("default", "file")["backup_enabled"], True)
        target = self.location()
        disabled = self.asset("disabled", "file", backup_enabled=False, backup_interval_days=7,
                              target_location_ids=[target["id"]])
        self.store.update("assets", disabled["id"], {"backup_enabled": True})
        self.assertEqual(self.store.update("assets", disabled["id"], {"backup_enabled": False}), disabled)
        self.assertEqual(self.mongo_factory(self.db).state(), self.store.state())
        self.assertIs(self.store.export()["assets"][1]["backup_enabled"], False)
        stored = self.store.database.assets.find_one({"id": disabled["id"]})
        self.assertIs(stored["backup_enabled"], False)


class MongoTargetContract(MongoFixture, target_contract.BackupTargetTests):
    contract_module = target_contract

    def test_mark_cancel_and_restore_preserve_policy_actual_history_and_storage_type(self):
        asset = self.asset("Letter", "file", backup_target=True, backup_enabled=False, backup_interval_days=7)
        payload = self.record_payload(asset)
        asset = self.store.update("assets", asset["id"], {"target_location_ids": [payload["location_id"]]})
        record = self.store.create("records", payload)
        self.assertEqual(self.store.update("assets", asset["id"], {"backup_target": False}), {**asset, "backup_target": False})
        self.assertEqual(self.mongo_factory(self.db).state(), self.store.state())
        self.assertEqual(self.store.state()["records"], [record])
        self.assertIs(self.store.database.assets.find_one({"id": asset["id"]})["backup_target"], False)
        self.assertEqual(self.store.update("assets", asset["id"], {"backup_target": True}), asset)


class MongoOrderContract(MongoFixture, order_contract.OrderTests):
    contract_module = order_contract


class MongoNativeTests(MongoFixture, unittest.TestCase):
    contract_module = store_contract

    def setUp(self):
        super().setUp()
        self.store = self.mongo_factory("native")

    def error(self, status, callback):
        with self.assertRaises(StoreError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def test_old_document_without_manual_order_loads_without_rewriting_facts(self):
        asset = self.store.create("assets", {"name": "legacy", "kind": "file"})
        self.store.database.assets.update_one({"id": asset["id"]}, {"$unset": {"manual_order": ""}})
        raw = self.store.database.assets.find_one({"id": asset["id"]})
        self.assertEqual(self.mongo_factory("native").state()["assets"], [asset])
        self.assertEqual(self.store.database.assets.find_one({"id": asset["id"]}), raw)
        self.assertEqual(self.store.update("assets", asset["id"], {"notes": "edited"}).get("manual_order"), None)

    def test_native_collections_indexes_and_public_export_whitelist(self):
        asset = self.store.create("assets", {"name": "manual", "kind": "file", "backup_target": True})
        version = self.store.create("versions", {"asset_id": asset["id"], "label": "Straße"})
        location = self.store.create("locations", {"name": "Straße", "kind": "nas"})
        self.store.create("records", {"version_id": version["id"], "location_id": location["id"],
                                      "backed_up_at": target_contract.STAMP})
        self.error(409, lambda: self.store.create("versions", {"asset_id": asset["id"], "label": "STRASSE"}))
        self.error(409, lambda: self.store.create("locations", {"name": "STRASSE", "kind": "nas"}))
        for collection in COLLECTIONS:
            raw = self.store.database[collection].find_one({})
            self.assertIsNotNone(raw)
            self.assertIsInstance(raw["_id"], str)
            self.assertEqual(str(uuid.UUID(raw["id"])), raw["id"])
            self.assertEqual(set(self.store.export()[collection][0]), {"id", "created_at", *(field for field in FIELDS[collection] if field != "manual_order")})
        self.assertTrue(any(item.get("unique") and list(item["key"].items()) == [("name_key", 1)]
                            for item in self.store.database.locations.list_indexes()))
        self.assertTrue(any(item.get("unique") and list(item["key"].items()) == [("asset_id", 1), ("label_key", 1)]
                            for item in self.store.database.versions.list_indexes()))

    def test_timestamp_order_and_equal_timestamp_import_ties_survive_reopen(self):
        timestamps = ["2026-10-11T08:00:00Z", "2026-10-11T08:00:00.000001Z", "2026-10-11T08:00:00.000002Z"]
        assets = [target_contract.imported_asset(name, "file", created_at=stamp)
                  for name, stamp in zip(("whole", "micro1", "micro2"), timestamps)]
        tie1 = target_contract.imported_asset("tie1", "file", created_at=timestamps[1])
        tie2 = target_contract.imported_asset("tie2", "file", created_at=timestamps[1])
        payload = target_contract.import_payload([assets[2], tie1, assets[0], tie2, assets[1]])
        self.store.import_data(payload)
        expected = ["whole", "tie1", "tie2", "micro1", "micro2"]
        self.assertEqual([entry["name"] for entry in self.store.state()["assets"]], expected)
        self.assertEqual([entry["name"] for entry in self.mongo_factory("native").state()["assets"]], expected)

    def test_import_constraint_conflict_is_atomic_after_prior_collection_inserts(self):
        existing = self.store.create("locations", {"name": "duplicate", "kind": "nas"})
        before = self.store.state()
        asset = target_contract.imported_asset("would-have-been-added", "file")
        payload = target_contract.import_payload([asset])
        payload["versions"] = [{"id": str(uuid.uuid4()), "asset_id": asset["id"], "label": "new",
                                "notes": "", "created_at": target_contract.STAMP}]
        payload["locations"] = [{**existing, "id": str(uuid.uuid4())}]
        self.error(409, lambda: self.store.import_data(payload))
        self.assertEqual(self.store.state(), before)

    def race(self, first, second):
        barrier = threading.Barrier(2)
        def run(action):
            barrier.wait(timeout=5)
            try:
                action()
                return 200
            except StoreError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, action) for action in (first, second)]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_opposite_moves_cannot_commit_a_cycle(self):
        left = self.store.create("assets", {"name": "left", "kind": "folder"})
        right = self.store.create("assets", {"name": "right", "kind": "folder"})
        other = self.mongo_factory("native")
        statuses = self.race(lambda: self.store.update("assets", left["id"], {"parent_id": right["id"]}),
                             lambda: other.update("assets", right["id"], {"parent_id": left["id"]}))
        self.assertEqual(sorted(statuses), [200, 400])
        assets = {entry["id"]: entry for entry in self.store.state()["assets"]}
        Store._asset_graph(assets)

    def test_concurrent_target_reference_and_delete_never_commit_dangling_reference(self):
        location = self.store.create("locations", {"name": "target", "kind": "nas"})
        asset = self.store.create("assets", {"name": "manual", "kind": "file"})
        other = self.mongo_factory("native")
        statuses = self.race(lambda: self.store.update("assets", asset["id"], {"target_location_ids": [location["id"]]}),
                             lambda: other.delete("locations", location["id"]))
        self.assertIn(200, statuses)
        self.assertNotEqual(statuses, [200, 200])
        state = self.store.state()
        location_ids = {entry["id"] for entry in state["locations"]}
        self.assertTrue(all(target in location_ids for entry in state["assets"] for target in entry["target_location_ids"]))

    def test_guard_is_first_mutation_command_and_real_write_conflict_retries(self):
        from pymongo import MongoClient
        from pymongo.monitoring import CommandListener
        conflict, locked, release = threading.Event(), threading.Event(), threading.Event()
        first_commands = []
        class Listener(CommandListener):
            def started(self, event):
                if event.command.get("startTransaction"):
                    first_commands.append((event.command_name, event.command.get(event.command_name)))
            def succeeded(self, event):
                pass
            def failed(self, event):
                if event.failure.get("code") == 112:
                    conflict.set()
        def observed_client(*args, **kwargs):
            return MongoClient(*args, **kwargs, event_listeners=[Listener()])
        with patch("backend.mongo_store.MongoClient", observed_client):
            other = self.mongo_factory("native")
        left = self.store.create("assets", {"name": "left", "kind": "folder"})
        right = self.store.create("assets", {"name": "right", "kind": "folder"})
        original = self.store._get
        def gated(session, collection, item_id):
            locked.set()
            if not release.wait(timeout=10):
                raise AssertionError("competing transaction never reached write guard")
            return original(session, collection, item_id)
        def second():
            try:
                other.update("assets", right["id"], {"parent_id": left["id"]})
            except StoreError as error:
                return error.status
            return 200
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.store, "_get", gated):
            first = pool.submit(self.store.update, "assets", left["id"], {"parent_id": right["id"]})
            try:
                self.assertTrue(locked.wait(timeout=5))
                competitor = pool.submit(second)
                self.assertTrue(conflict.wait(timeout=5), "real Mongo write conflict did not occur")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)["parent_id"], right["id"])
            self.assertEqual(competitor.result(timeout=10), 400)
        self.assertGreaterEqual(len(first_commands), 3)  # readiness, failed mutation, retry
        self.assertTrue(all(command == ("update", "_meta") for command in first_commands))

    def test_state_and_export_hold_snapshot_across_all_collections(self):
        original = self.store._read_collection
        other = self.mongo_factory("native")
        new = target_contract.imported_asset("snapshot-child", "file")
        payload = target_contract.import_payload([new])
        target_contract.add_history(payload, new)
        for operation in (self.store.state, self.store.export):
            with self.subTest(operation=operation.__name__):
                self.store.database.assets.delete_many({})
                self.store.database.versions.delete_many({})
                self.store.database.locations.delete_many({})
                self.store.database.records.delete_many({})
                written = False
                def gated(collection, session):
                    nonlocal written
                    if collection == "versions" and not written:
                        other.import_data(payload)
                        written = True
                    return original(collection, session)
                with patch.object(self.store, "_read_collection", gated):
                    snapshot = operation()
                self.assertTrue(written)
                self.assertEqual({key: snapshot[key] for key in COLLECTIONS}, {key: [] for key in COLLECTIONS})
                self.assertEqual(len(other.state()["records"]), 1)

    def test_readiness_requires_real_session_transaction_and_sanitizes_failure(self):
        self.assertTrue(self.store.readiness())
        with patch.object(self.store.client, "start_session", side_effect=RuntimeError("secret-uri@private-host")):
            with self.assertRaises(StoreError) as caught:
                self.store.readiness()
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn("secret-uri", str(caught.exception))
        self.assertNotIn("private-host", str(caught.exception))

    def test_missing_replica_set_and_operation_driver_errors_are_sanitized(self):
        from pymongo.errors import PyMongoError
        with patch.object(type(self.store.client.admin), "command", return_value={
            "isWritablePrimary": True, "logicalSessionTimeoutMinutes": 30,
        }):
            self.error(503, self.store.readiness)
        for operation in (self.store.state, self.store.export,
                          lambda: self.store.create("assets", {"name": "x", "kind": "file"})):
            with patch.object(self.store.client, "start_session", side_effect=PyMongoError("password@private-host")):
                with self.assertRaises(StoreError) as caught:
                    operation()
                self.assertEqual(caught.exception.status, 503)
                self.assertNotIn("password", str(caught.exception))
                self.assertNotIn("private-host", str(caught.exception))


class MongoRestrictedAccountTests(unittest.TestCase):
    """Optional auth replica set; credentials are loaded privately, never logged.

    DATABACK_TEST_MONGO_AUTH_URI_FILE points to JSON with a ``root_uri`` member.
    The test creates and removes a readWrite-only user in its own test database.
    """

    def test_readwrite_only_account_passes_readiness_and_cannot_run_admin_status(self):
        config_path = os.environ.get("DATABACK_TEST_MONGO_AUTH_URI_FILE")
        if not config_path:
            self.skipTest("DATABACK_TEST_MONGO_AUTH_URI_FILE is not configured")
        from pymongo import MongoClient
        from pymongo.errors import OperationFailure
        from backend.storage import create_store
        root_uri = json.loads(Path(config_path).read_text(encoding="utf-8"))["root_uri"]
        database = "databack_test_" + uuid.uuid4().hex
        username, password = "test_app_" + secrets.token_hex(8), secrets.token_urlsafe(48)
        root = MongoClient(root_uri, serverSelectionTimeoutMS=5000)
        root[database].command("createUser", username, pwd=password, roles=[{"role": "readWrite", "db": database}])
        store = None
        try:
            parts = urlsplit(root_uri)
            query = [(key, value) for key, value in parse_qsl(parts.query) if key != "authSource"]
            query.append(("authSource", database))
            authority = quote(username, safe="") + ":" + quote(password, safe="") + "@" + parts.netloc.rsplit("@", 1)[-1]
            app_uri = urlunsplit((parts.scheme, authority, parts.path, urlencode(query), ""))
            store = create_store(mongo_uri=app_uri, mongo_database=database)
            self.assertTrue(store.readiness())
            with self.assertRaises(OperationFailure) as caught:
                store.client.admin.command("replSetGetStatus")
            self.assertEqual(caught.exception.code, 13)
            item = store.create("assets", {"name": "manual", "kind": "file", "source_path": "//offline.invalid/manual"})
            self.assertEqual(store.state()["assets"], [item])
            self.assertEqual(store.import_data(store.export())["added"]["assets"], 0)
        finally:
            if store is not None:
                store.close()
            root[database].command("dropUser", username)
            root.drop_database(database)
            root.close()


if __name__ == "__main__":
    unittest.main()
