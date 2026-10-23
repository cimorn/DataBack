"""Native transactional MongoDB persistence for manually entered ledger facts.

All constraint reads happen after the same guard document has been written in
the mutation transaction. This prevents snapshot-isolation write skew across
documents (for example opposite parent moves or deleting a selected location).
PyMongo retries transactions that lose that write race using a fresh snapshot.
"""

import uuid
from datetime import datetime, timezone

from pymongo import MongoClient, ReadPreference, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .store import (
    COLLECTIONS, FIELDS, FIELD_NAMES, StoreError, infer_parents, normalize_time,
    utc_now, validate_asset_graph, validate_collection, validate_entry, validate_reorder, appended_order, validate_batch_mark,
)
from .recovery import RecoveryMixin, make_trash


UNAVAILABLE = "MongoDB 存储暂不可用，请检查部署配置和副本集状态。"
CONFLICT = "名称或版本名称已存在，或关联条目发生冲突。"
GUARD = "mutation"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class MongoStore(RecoveryMixin):
    backend = "mongodb"

    def __init__(self, mongo_uri, mongo_database="databack"):
        self.client = None
        try:
            self.client = MongoClient(
                mongo_uri, appname="databack", serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000, socketTimeoutMS=15000, retryWrites=True,
            )
            self.database = self.client.get_database(mongo_database)
            self._hello()
            self._initialize()
            self.readiness()
        except Exception:
            self.close()
            raise StoreError(UNAVAILABLE, 503) from None

    def _hello(self):
        # hello is available to a database readWrite account. replSetGetStatus
        # would need unnecessary administrative privileges.
        hello = self.client.admin.command("hello")
        if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None or not hello.get("isWritablePrimary"):
            raise StoreError(UNAVAILABLE, 503)

    def _initialize(self):
        for collection in COLLECTIONS:
            self.database[collection].create_index("id", unique=True)
            self.database[collection].create_index([("_created_order", 1), ("_sequence", 1)])
        self.database.assets.create_index("parent_id")
        self.database.assets.create_index("target_location_ids")
        self.database.versions.create_index([("asset_id", 1), ("label_key", 1)], unique=True)
        self.database.locations.create_index("name_key", unique=True)
        self.database.records.create_index("version_id")
        self.database.records.create_index("location_id")
        self.database.trash.create_index('id', unique=True)
        self.database.trash.create_index('expires_at')
        self.database["_meta"].update_one(
            {"_id": GUARD}, {"$setOnInsert": {"revision": 0, "sequence": 0}}, upsert=True,
        )

    def close(self):
        if self.client is not None:
            self.client.close()

    def _transaction(self, callback, *, mutation=False):
        def operation(session):
            if mutation:
                # This MUST be the first database command of each mutation:
                # any competing writer must retry before reading constraints.
                result = self.database["_meta"].update_one(
                    {"_id": GUARD}, {"$inc": {"revision": 1}}, session=session,
                )
                if result.matched_count != 1:
                    raise StoreError(UNAVAILABLE, 503)
            return callback(session)

        try:
            with self.client.start_session() as session:
                return session.with_transaction(
                    operation, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY,
                )
        except DuplicateKeyError:
            raise StoreError(CONFLICT, 409) from None
        except PyMongoError:
            raise StoreError(UNAVAILABLE, 503) from None

    def readiness(self):
        try:
            self._hello()
            # A committed guard increment plus read-back proves transactions and
            # application write access, beyond a successful connection/ping.
            guard = self._transaction(
                lambda session: self.database["_meta"].find_one({"_id": GUARD}, session=session),
                mutation=True,
            )
            if guard is None or guard.get("revision", 0) < 1:
                raise StoreError(UNAVAILABLE, 503)
            return True
        except Exception:
            raise StoreError(UNAVAILABLE, 503) from None

    @staticmethod
    def _public(collection, row):
        return {field: row.get(field, False) if field == "private" else row[field]
                for field in ("id", *FIELDS[collection], "created_at")
                if field != "manual_order" or row.get(field) is not None}

    def _get(self, session, collection, item_id):
        validate_collection(collection)
        row = self.database[collection].find_one({"id": item_id}, session=session)
        if row is None:
            raise StoreError("条目不存在，可能已被删除。", 404)
        return self._public(collection, row)

    def _read_collection(self, collection, session):
        rows = self.database[collection].find({}, session=session).sort([("_created_order", 1), ("_sequence", 1)])
        return [self._public(collection, row) for row in rows]

    def _state(self, session):
        return {collection: self._read_collection(collection, session) for collection in COLLECTIONS}

    def state(self):
        return self._transaction(self._state)

    def export(self):
        return self._transaction(lambda session: {
            "format": "filebackup-ledger", "schema_version": 3,
            "exported_at": utc_now(), **self._state(session),
        })

    def _references(self, session, collection, entry, status=404, effective=None):
        def target_entry(target, item_id, field):
            row = (effective[target].get(item_id) if effective is not None else
                   self.database[target].find_one({"id": item_id}, session=session))
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
        relations = {"versions": (("asset_id", "assets"),),
                     "records": (("version_id", "versions"), ("location_id", "locations"))}
        for field, target in relations.get(collection, ()):
            target_entry(target, entry[field], field)

    def _asset_map(self, session):
        return {row["id"]: row for row in self.database.assets.find(
            {}, {"id": 1, "kind": 1, "parent_id": 1}, session=session,
        )}

    def _has_children(self, session, item_id):
        return self.database.assets.find_one({"parent_id": item_id}, {"_id": 1}, session=session) is not None

    @staticmethod
    def _stored_values(collection, entry):
        values = dict(entry)
        if collection == "assets":
            values["manual_order"] = entry.get("manual_order")
        elif collection == "versions":
            values["label_key"] = entry["label"].casefold()
        elif collection == "locations":
            values["name_key"] = entry["name"].casefold()
        return values

    def _insert(self, session, collection, entry):
        values = self._stored_values(collection, entry)
        guard = self.database["_meta"].find_one_and_update(
            {"_id": GUARD}, {"$inc": {"sequence": 1}}, session=session,
            return_document=ReturnDocument.AFTER,
        )
        delta = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00")) - EPOCH
        # BSON dates truncate microseconds; integers preserve the public order.
        values.update(_id=entry["id"], _sequence=guard["sequence"],
                      _created_order=(delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)
        self.database[collection].insert_one(values, session=session)
        return self._public(collection, values)

    def _create(self, session, collection, payload):
        entry = validate_entry(collection, payload)
        if collection == "assets" and "manual_order" not in payload:
            position = appended_order(self.database.assets.find({"parent_id": entry["parent_id"]}, session=session), entry["parent_id"])
            if position is not None:
                entry["manual_order"] = position
        entry.update(id=str(uuid.uuid4()), created_at=utc_now())
        self._references(session, collection, entry)
        if collection == "records":
            version = self.database.versions.find_one({"id": entry["version_id"]}, session=session)
            asset = self.database.assets.find_one({"id": version["asset_id"]}, session=session)
            if self._has_children(session, asset["id"]):
                raise StoreError("只有末级资料可以登记新的备份。", 409)
            if not asset["backup_target"]:
                raise StoreError("请先将资料设为备份对象。", 409)
        if collection == "assets":
            validate_asset_graph({**self._asset_map(session), entry["id"]: entry})
        return self._insert(session, collection, entry)

    def create(self, collection, payload):
        return self._transaction(lambda session: self._create(session, collection, payload), mutation=True)

    def create_batch(self, records, *, mark_asset_id=None):
        if not isinstance(records, list) or not records:
            raise StoreError("请至少登记一条备份记录。")
        def operation(session):
            if mark_asset_id is not None:
                validate_batch_mark(records, mark_asset_id, lambda collection, item_id: self._get(session, collection, item_id))
                self.database.assets.update_one({"id": mark_asset_id}, {"$set": {"backup_target": True}}, session=session)
            return [self._create(session, "records", payload) for payload in records]
        return self._transaction(operation, mutation=True)

    def update(self, collection, item_id, payload):
        def operation(session):
            current = self._get(session, collection, item_id)
            if not isinstance(payload, dict) or set(payload) - (set(FIELDS[collection]) - {"private"}):
                raise StoreError("修改内容必须是对象，且只能包含可编辑字段。")
            writable = {key: current[key] for key in FIELDS[collection] if key in current and key != "private"}
            writable.update(payload)
            values = validate_entry(collection, writable)
            if collection == "assets":
                values["private"] = current["private"]
            self._references(session, collection, {**values, "id": item_id})
            if collection == "assets" and values["parent_id"] != current["parent_id"] and "manual_order" not in payload:
                values["manual_order"] = appended_order(self.database.assets.find({"parent_id": values["parent_id"], "id": {"$ne": item_id}}, session=session), values["parent_id"])
            if collection == "assets":
                has_children = self._has_children(session, item_id)
                if values["kind"] == "file" and has_children:
                    raise StoreError("该文件夹仍有子项，不能改为文件。", 409)
                if has_children and values["backup_target"] and not current["backup_target"]:
                    raise StoreError("请标记需要备份的末级文件或文件夹。", 409)
                validate_asset_graph({**self._asset_map(session), item_id: {**values, "id": item_id}})
            self.database[collection].update_one(
                {"id": item_id}, {"$set": self._stored_values(collection, values)}, session=session,
            )
            return self._get(session, collection, item_id)
        return self._transaction(operation, mutation=True)

    def set_private(self, item_id, private):
        if type(private) is not bool:
            raise StoreError("隐私标记必须是布尔值。")
        def operation(session):
            self._get(session, "assets", item_id)
            self.database.assets.update_one({"id": item_id}, {"$set": {"private": private}}, session=session)
            return {"changed": True}
        return self._transaction(operation, mutation=True)

    def reorder_assets(self, parent_id, ordered_ids):
        def operation(session):
            ids = validate_reorder(self._asset_map(session), parent_id, ordered_ids)
            for index, item_id in enumerate(ids):
                self.database.assets.update_one({"id": item_id}, {"$set": {"manual_order": index}}, session=session)
            return {"ok": True}
        return self._transaction(operation, mutation=True)

    def delete(self, collection, item_id):
        def operation(session):
            self._get(session, collection, item_id)
            if collection == "assets" and self._has_children(session, item_id):
                raise StoreError("该文件夹仍有子项，请先移动或删除子项。", 409)
            if collection == "locations" and self.database.assets.find_one(
                {"target_location_ids": item_id}, {"_id": 1}, session=session,
            ):
                raise StoreError("该位置仍被资料选作目标备份位置，请先取消选择。", 409)
            if collection == "assets":
                version_ids = [row["id"] for row in self.database.versions.find(
                    {"asset_id": item_id}, {"id": 1}, session=session,
                )]
                relation = {"version_id": {"$in": version_ids}}
            else:
                relation = {"versions": {"version_id": item_id}, "locations": {"location_id": item_id}}.get(collection)
            if relation is not None and self.database.records.find_one(relation, {"_id": 1}, session=session):
                raise StoreError("该条目仍有关联备份记录，请先删除相关记录。", 409)
            tombstone = make_trash(self._state(session), collection, item_id)
            self.database.trash.insert_one(dict(tombstone), session=session)
            if collection == "assets":
                self.database.versions.delete_many({"asset_id": item_id}, session=session)
            self.database[collection].delete_one({"id": item_id}, session=session)
            return {"deleted": True, "trash_id": tombstone['id']}
        return self._transaction(operation, mutation=True)

    def _run_recovery(self, callback):
        return self._transaction(callback, mutation=True)

    def _replace_fields(self, session, collection, item_id, entry):
        self.database[collection].update_one({'id': item_id}, {'$set': self._stored_values(collection, entry)}, session=session)

    def _read_trash(self, session):
        return list(self.database.trash.find({}, {'_id': 0}, session=session).sort('deleted_at', -1))

    def _prune_trash(self, session, now):
        self.database.trash.delete_many({'expires_at': {'$lte': now}}, session=session)

    def _remove_trash(self, session, trash_id):
        self.database.trash.delete_one({'id': trash_id}, session=session)

    def _clear_state(self, session):
        for collection in COLLECTIONS:
            self.database[collection].delete_many({}, session=session)

    def import_data(self, payload, *, require_empty=False):
        expected = {"format", "schema_version", "exported_at", *COLLECTIONS}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise StoreError("导入文件格式不完整，或包含不支持的字段。")
        if payload["format"] != "filebackup-ledger" or type(payload["schema_version"]) is not int or payload["schema_version"] not in (1, 2, 3):
            raise StoreError("该文件不是受支持的台账导出文件（格式版本 1、2 或 3）。")
        if require_empty and payload["schema_version"] != 3:
            raise StoreError("迁移须使用格式版本 3 的完整台账快照。")
        normalize_time(payload["exported_at"], "created_at")
        incoming, seen = {}, set()
        for collection in COLLECTIONS:
            if not isinstance(payload[collection], list):
                raise StoreError("导入文件中的各类条目必须是数组。")
            incoming[collection] = []
            for raw in payload[collection]:
                entry = validate_entry(collection, raw, importing=True, schema_version=payload["schema_version"])
                if require_empty:
                    # Migration validates with the normal rules, but retains
                    # every original value (including raw paths and offsets).
                    entry = dict(raw)
                    if collection == 'assets':
                        entry.setdefault('private', False)
                if entry["id"] in seen:
                    raise StoreError("导入文件包含重复的条目 ID。")
                seen.add(entry["id"])
                incoming[collection].append(entry)

        def operation(session):
            # _transaction has already written the shared mutation guard.
            # Check inside that transaction, before any public-data write, so
            # two migrations or an API writer cannot both observe emptiness.
            if require_empty and any(self.database[collection].find_one({}, {"_id": 1}, session=session)
                                     is not None for collection in COLLECTIONS):
                raise StoreError("迁移目标台账必须为空；现有数据不会被覆盖。", 409)
            # Retry callbacks must own their mutable inferred fields and counts.
            entries = {collection: [dict(entry) for entry in items] for collection, items in incoming.items()}
            added = {collection: 0 for collection in COLLECTIONS}
            existing = {collection: {entry["id"]: entry for entry in items} for collection, items in self._state(session).items()}
            effective = {collection: dict(items) for collection, items in existing.items()}
            for collection in COLLECTIONS:
                for entry in entries[collection]:
                    if any(entry["id"] in existing[other] for other in COLLECTIONS if other != collection):
                        raise StoreError("导入条目 ID 与当前其他类型的条目冲突。", 409)
                    if entry["id"] not in existing[collection]:
                        effective[collection][entry["id"]] = entry
            if payload["schema_version"] == 1:
                infer_parents(effective["assets"], set(effective["assets"]) - set(existing["assets"]))
            for collection in COLLECTIONS:
                for entry in entries[collection]:
                    self._references(session, collection, entry, status=409, effective=effective)
            validate_asset_graph(effective["assets"], status=409)
            if payload["schema_version"] in (1, 2):
                parents = {entry["parent_id"] for entry in effective["assets"].values() if entry["parent_id"] is not None}
                recorded_assets = {effective["versions"][record["version_id"]]["asset_id"]
                                   for record in effective["records"].values()}
                for entry in entries["assets"]:
                    if entry["id"] not in existing["assets"]:
                        entry["backup_target"] = entry["id"] in recorded_assets and entry["id"] not in parents
            for collection in COLLECTIONS:
                for entry in entries[collection]:
                    if entry["id"] not in existing[collection]:
                        self._insert(session, collection, entry)
                        added[collection] += 1
            if require_empty and self._state(session) != entries:
                # Exact public verification is part of the import transaction;
                # list order or value mismatch must abort rather than commit.
                raise StoreError("迁移快照与导入结果不一致；事务已取消。", 409)
            return {"added": added}
        return self._transaction(operation, mutation=True)
