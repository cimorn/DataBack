"""Deliberate schema-3 migration. Dry-run never writes to MongoDB.

Run inside the Docker private network; credentials are environment-only.
An apply requires a matching receipt from a successful read-only dry-run.
Only counts and the canonical four-collection digest are printed.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Also support the documented direct ``python scripts/migrate_to_mongo.py``.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient, ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from backend.mongo_store import MongoStore
from backend.store import COLLECTIONS, normalize_time, validate_asset_graph, validate_entry
from runtime.configuration import mongo_uri_from_env


class MigrationError(ValueError):
    """Fixed, sanitized operator diagnostics."""


def canonical_summary(payload):
    public = {collection: payload[collection] for collection in COLLECTIONS}
    encoded = json.dumps(public, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {"counts": {key: len(value) for key, value in public.items()},
            "sha256": hashlib.sha256(encoded).hexdigest()}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("Snapshot JSON has duplicate keys.")
        result[key] = value
    return result


def load_snapshot(path):
    try:
        with Path(path).open(encoding="utf-8") as file:
            payload = json.load(file, object_pairs_hook=_unique_object,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(payload, dict) or set(payload) != {"format", "schema_version", "exported_at", *COLLECTIONS}
                or payload["format"] != "filebackup-ledger" or type(payload["schema_version"]) is not int
                or payload["schema_version"] != 3):
            raise ValueError()
        normalize_time(payload["exported_at"], "created_at")
        seen, entries = set(), {}
        for collection in COLLECTIONS:
            if not isinstance(payload[collection], list):
                raise ValueError()
            entries[collection] = {}
            for raw in payload[collection]:
                entry = validate_entry(collection, raw, importing=True, schema_version=3)
                if entry["id"] in seen:
                    raise ValueError()
                seen.add(entry["id"])
                entries[collection][entry["id"]] = entry
                if collection == 'assets':
                    # Old schema-3 snapshots predate privacy. Normalize only the
                    # optional default before dry-run/apply digest comparisons.
                    raw.setdefault('private', False)
        validate_asset_graph(entries["assets"])
        for entry in entries["assets"].values():
            if any(target not in entries["locations"] for target in entry["target_location_ids"]):
                raise ValueError()
        labels, names = set(), set()
        for entry in entries["versions"].values():
            key = (entry["asset_id"], entry["label"].casefold())
            if entry["asset_id"] not in entries["assets"] or key in labels:
                raise ValueError()
            labels.add(key)
        for entry in entries["locations"].values():
            key = entry["name"].casefold()
            if key in names:
                raise ValueError()
            names.add(key)
        for entry in entries["records"].values():
            if entry["version_id"] not in entries["versions"] or entry["location_id"] not in entries["locations"]:
                raise ValueError()
        return payload
    except Exception:
        raise MigrationError("Snapshot is invalid; use a complete schema-3 export.") from None


def _read_only_target(client, database):
    hello = client.admin.command("hello")
    if not hello.get("isWritablePrimary") or not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        raise MigrationError("MongoDB replica set is not ready.")
    topology = {"replica_set": hello["setName"], "hosts": sorted(hello.get("hosts", []))}
    target = hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest()
    def read(session):
        return {collection: [MongoStore._public(collection, row) for row in
                            client[database][collection].find({}, session=session).sort(
                                [("_created_order", 1), ("_sequence", 1)])]
                for collection in COLLECTIONS}
    with client.start_session() as session:
        state = session.with_transaction(read, read_concern=ReadConcern("snapshot"),
                                        write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY)
    return state, target


def _write_receipt(path, receipt):
    # This file contains only the public summary and a topology fingerprint.
    # It never contains a URI, credentials, paths from entries or ledger data.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(receipt, file, sort_keys=True)
        file.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only dry-run followed by an atomic empty-ledger migration.")
    parser.add_argument("snapshot", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", type=Path, help="Defaults to SNAPSHOT.dry-run.json")
    args = parser.parse_args(argv)
    client, store = None, None
    try:
        payload = load_snapshot(args.snapshot)
        summary = canonical_summary(payload)
        database = os.environ.get("MONGODB_DATABASE", "databack")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", database):
            raise MigrationError("MongoDB database configuration is invalid.")
        receipt_path = args.receipt or args.snapshot.with_suffix(".dry-run.json")
        # Refuse an unconfirmed apply before constructing a mutating adapter.
        receipt = None
        if args.apply:
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except Exception:
                raise MigrationError("Apply requires a successful prior dry-run receipt.") from None
        client = MongoClient(mongo_uri_from_env(), appname="databack-migration", serverSelectionTimeoutMS=5000,
                             connectTimeoutMS=5000, socketTimeoutMS=15000)
        state, target = _read_only_target(client, database)
        expected_receipt = {"version": 1, "database": database, "target": target, **summary}
        if any(state[collection] for collection in COLLECTIONS):
            raise MigrationError("Migration target must have no public ledger entries.")
        if args.dry_run:
            _write_receipt(receipt_path, expected_receipt)
            print(json.dumps({"mode": "dry-run", **summary}, sort_keys=True))
            return 0
        if receipt != expected_receipt:
            raise MigrationError("Dry-run receipt does not match this snapshot and target.")
        store = MongoStore(mongo_uri_from_env(), database)
        store.import_data(payload, require_empty=True)
        # The adapter already checks exact data inside the transaction. This
        # second coherent read is the operator's post-commit count/digest check.
        actual = canonical_summary(store.state())
        if actual != summary:
            raise MigrationError("Post-import verification differs; stop writers and inspect the ledger.")
        print(json.dumps({"mode": "apply", **actual}, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print("Migration interrupted; verify status with a read-only connection before retrying.", file=sys.stderr)
        return 130
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print("Migration failed; check snapshot, empty target and MongoDB deployment configuration.", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
