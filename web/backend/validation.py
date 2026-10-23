"""Shared ledger validation and identity rules for all storage operations."""

import re
import uuid
from datetime import datetime, timezone

COLLECTIONS = ("assets", "versions", "locations", "records")
FIELDS = {
    "assets": ("name", "kind", "source_path", "notes", "parent_id", "backup_interval_days", "target_location_ids", "backup_enabled", "backup_target", "manual_order", "private"),
    "versions": ("asset_id", "label", "notes"),
    "locations": ("name", "kind", "base_path", "notes"),
    "records": ("version_id", "location_id", "backed_up_at", "stored_path", "notes"),
}
DEFAULTS = {"source_path": "", "base_path": "", "stored_path": "", "notes": "",
            "parent_id": None, "backup_interval_days": 30, "target_location_ids": [], "backup_enabled": True, "backup_target": False, "manual_order": None, "private": False}
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
FIELD_NAMES = {
    "name": "名称", "kind": "类型", "source_path": "原始路径", "base_path": "基础路径",
    "stored_path": "存放路径", "notes": "备注", "label": "版本名称", "asset_id": "资料关联",
    "version_id": "版本关联", "location_id": "位置关联", "backed_up_at": "备份时间", "created_at": "创建时间",
    "parent_id": "父文件夹", "backup_interval_days": "备份间隔", "target_location_ids": "目标备份位置",
    "backup_enabled": "备份开关", "backup_target": "备份对象标记", "manual_order": "手动排序", "private": "隐私标记",
}


class StoreError(Exception):
    """A user-facing, Chinese validation error with its HTTP status."""

    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_time(value, field="backed_up_at"):
    if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
        raise StoreError(f"{FIELD_NAMES[field]}须为带时区的 ISO 时间，例如 2026-10-11T08:00:00+08:00。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # fromisoformat accepts offset components over 59; reject these explicitly.
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("invalid offset")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise StoreError(f"{FIELD_NAMES[field]}无效，请检查日期、时间和时区。") from None


def validate_id(value):
    if not isinstance(value, str):
        raise StoreError("条目 ID 必须是 UUID 字符串。")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("non-canonical UUID")
    except ValueError:
        raise StoreError("条目 ID 必须是标准 UUID 字符串。") from None
    return value


def infer_parents(assets, new_ids):
    """Infer legacy links from stored strings only; never inspect source paths."""
    def path_key(value):
        if not value:
            return None
        value = value.replace("\\", "/").casefold()
        anchor = "//" if value.startswith("//") else "/" if value.startswith("/") else ""
        return anchor, tuple(part for part in value.split("/") if part)

    paths = {item_id: path_key(entry["source_path"]) for item_id, entry in assets.items()}
    folders = {}
    for item_id, entry in assets.items():
        if entry["kind"] == "folder" and paths[item_id] is not None:
            folders.setdefault(paths[item_id], []).append(item_id)
    for item_id, entry in assets.items():
        if item_id not in new_ids or paths[item_id] is None:
            continue
        anchor, parts = paths[item_id]
        # Equal paths are never parent/child. Stop at the nearest match even
        # when it is ambiguous; guessing a more distant parent would hide it.
        for depth in range(len(parts) - 1, -1, -1):
            candidates = folders.get((anchor, parts[:depth]), [])
            if not candidates:
                continue
            if len(candidates) == 1:
                parent_id = candidates[0]
                cursor, seen = parent_id, {item_id}
                while cursor is not None and cursor not in seen:
                    seen.add(cursor)
                    cursor = assets[cursor]["parent_id"]
                if cursor is None:
                    entry["parent_id"] = parent_id
            break


def validate_collection(collection):
    if collection not in COLLECTIONS:
        raise StoreError("未找到该数据类型。", 404)


def validate_entry(collection, payload, importing=False, schema_version=3):
    validate_collection(collection)
    if not isinstance(payload, dict):
        raise StoreError("条目必须是 JSON 对象。")
    fields = FIELDS[collection]
    if collection == "assets" and (not importing or schema_version in (1, 2)):
        fields = tuple(field for field in fields if field != "private")
    if importing and collection == "assets":
        if schema_version == 1:
            fields = fields[:4]
        elif schema_version == 2:
            fields = tuple(field for field in fields if field != "backup_target")
    allowed = set(fields) | ({"id", "created_at"} if importing else set())
    if set(payload) - allowed:
        raise StoreError("条目包含不支持的字段。")
    # Earlier schema 2 exports predate the reminder switch. Schema 3
    # requires both booleans; schemas 1 and 2 never accept backup_target.
    required = allowed - {"manual_order", "backup_enabled", "private"} if importing and collection == "assets" and schema_version == 2 else allowed - {"manual_order", "private"}
    if importing and not required.issubset(payload):
        raise StoreError("导入条目缺少必要字段。")
    result = {}
    for field in FIELDS[collection]:
        if field not in payload and field not in DEFAULTS:
            raise StoreError(f"请填写{FIELD_NAMES[field]}。")
        value = payload.get(field, DEFAULTS.get(field))
        if field == "manual_order":
            if field in payload and (type(value) is not int or not 0 <= value <= 2147483647):
                raise StoreError("手动排序须为 0 到 2147483647 之间的整数。")
            if field in payload:
                result[field] = value
            continue
        if field in ("backup_enabled", "backup_target", "private"):
            if type(value) is not bool:
                raise StoreError(f"{FIELD_NAMES[field]}必须是布尔值（true 或 false）。")
            result[field] = value
            continue
        if field == "parent_id":
            result[field] = None if value is None else validate_id(value)
            continue
        if field == "backup_interval_days":
            if type(value) is not int or not 1 <= value <= 3650:
                raise StoreError("备份间隔须为 1 到 3650 天之间的整数。")
            result[field] = value
            continue
        if field == "target_location_ids":
            if not isinstance(value, list):
                raise StoreError("目标备份位置须为 UUID 字符串数组。")
            targets = [validate_id(target_id) for target_id in value]
            if len(targets) != len(set(targets)):
                raise StoreError("目标备份位置不能重复。")
            result[field] = targets
            continue
        if not isinstance(value, str):
            raise StoreError(f"{FIELD_NAMES[field]}必须是文字。")
        if "\x00" in value:
            raise StoreError(f"{FIELD_NAMES[field]}包含无效字符。")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise StoreError(f"{FIELD_NAMES[field]}包含无效文字。") from None
        maximum = 20000 if field == "notes" else 8192 if field.endswith("path") else 200
        if len(value) > maximum:
            raise StoreError(f"{FIELD_NAMES[field]}过长（最多 {maximum} 个字符）。")
        if field in ("name", "label"):
            value = value.strip()
            if not value:
                raise StoreError(f"请填写{FIELD_NAMES[field]}。")
        elif field.endswith("_id"):
            value = validate_id(value)
        elif field == "backed_up_at":
            value = normalize_time(value)
        result[field] = value
    if collection == "assets" and result["kind"] not in ("file", "folder"):
        raise StoreError("资料类型只能是文件或文件夹。")
    if collection == "locations" and result["kind"] not in ("local", "external", "cloud", "nas", "other"):
        raise StoreError("备份位置类型无效。")
    if importing:
        result["id"] = validate_id(payload["id"])
        result["created_at"] = normalize_time(payload["created_at"], "created_at")
    return result

def validate_batch_mark(records, asset_id, get_entry):
    """A combined marker write must belong to every record in its transaction."""
    validate_id(asset_id)
    get_entry("assets", asset_id)
    for payload in records:
        entry = validate_entry("records", payload)
        version = get_entry("versions", entry["version_id"])
        if version["asset_id"] != asset_id:
            raise StoreError("备份记录必须属于要标记的资料。", 409)


def validate_asset_graph(assets, status=400):
    completed = set()
    for item_id in assets:
        cursor, visiting = item_id, set()
        while cursor is not None and cursor not in completed:
            if cursor in visiting:
                raise StoreError("父子关系不能形成循环，不能将文件夹移到自身或其子项下面。", status)
            visiting.add(cursor)
            entry = assets[cursor]
            parent_id = entry["parent_id"]
            if parent_id is not None and (parent_id not in assets or assets[parent_id]["kind"] != "folder"):
                raise StoreError("父条目不存在或不是文件夹。", status)
            cursor = parent_id
        completed.update(visiting)

def validate_reorder(assets, parent_id, ordered_ids):
    if parent_id is not None:
        validate_id(parent_id)
        parent = assets.get(parent_id)
        if parent is None or parent["kind"] != "folder":
            raise StoreError("父条目不存在或不是文件夹。", 409)
    if not isinstance(ordered_ids, list):
        raise StoreError("排序须为完整的条目 ID 数组。")
    ids = [validate_id(item_id) for item_id in ordered_ids]
    siblings = {item_id for item_id, asset in assets.items() if asset.get("parent_id") == parent_id}
    if len(ids) != len(set(ids)) or set(ids) != siblings:
        raise StoreError("目录条目已变化，请刷新后按完整目录重新排序。", 409)
    return ids


def appended_order(assets, parent_id):
    positions = [asset.get("manual_order") for asset in assets if asset.get("parent_id") == parent_id and asset.get("manual_order") is not None]
    if not positions:
        return None
    position = max(positions) + 1
    if position > 2147483647:
        raise StoreError("手动排序已达到上限，请重新排序目录后再添加。", 409)
    return position


