import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.server import MAX_BODY_BYTES, make_server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<!doctype html><title>本地台账</title>", encoding="utf-8")
        (self.dist / "app.js").write_text("console.log('ledger')", encoding="utf-8")
        (self.root / "private.txt").write_text("must stay private", encoding="utf-8")
        self.server = make_server(port=0, db_path=self.root / "ledger.sqlite3", static_dir=self.dist)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        values = {"Content-Type": "application/json"} if method in ("POST", "PUT", "DELETE") else {}
        values.update(headers or {})
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else raw
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type", "")
        decoded = json.loads(data) if "application/json" in content_type else data.decode("utf-8")
        result = response.status, decoded, dict(response.getheaders())
        connection.close()
        return result

    def create(self, collection, payload):
        status, value, _ = self.request("POST", "/api/" + collection, payload)
        self.assertEqual(status, 201, value)
        return value

    def test_export_uses_relative_virtual_paths_and_roundtrips_without_live_mutation(self):
        from backend.store import Store
        root = self.create("assets", {"name": "个人文件夹", "kind": "folder", "source_path": r"\\fixture-nas\personal_folder"})
        photos = self.create("assets", {"name": "Photos", "kind": "folder", "parent_id": root["id"], "backup_target": True, "source_path": r"\\\\fixture-nas\\personal_folder\\Photos"})
        version = self.create("versions", {"asset_id": photos["id"], "label": "old-label"})
        location = self.create("locations", {"name": "NAS", "kind": "nas", "base_path": r"\\fixture-nas\backup\Photos"})
        record = self.create("records", {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-16T10:00:00Z", "stored_path": r"E:\\archive\\Photos", "notes": "备份事实保留"})
        before = self.request("GET", "/api/state")[1]
        status, exported, _ = self.request("GET", "/api/export")
        self.assertEqual(status, 200)
        indexed = {entry["id"]: entry for entry in exported["assets"]}
        self.assertEqual(indexed[root["id"]]["source_path"], "个人文件夹")
        self.assertEqual(indexed[photos["id"]]["source_path"], "个人文件夹/Photos")
        self.assertEqual(exported["locations"][0]["base_path"], "backup/Photos")
        self.assertEqual(exported["records"][0]["stored_path"], "archive/Photos")
        self.assertEqual(self.request("GET", "/api/state")[1], before)
        restored = Store(self.root / "restored.sqlite3")
        self.assertEqual(restored.import_data(exported)["added"]["records"], 1)
        self.assertEqual(restored.state()["records"][0]["id"], record["id"])
        self.assertEqual(restored.state()["records"][0]["notes"], "备份事实保留")
        self.assertEqual(restored.state()["assets"][1]["parent_id"], root["id"])

    def test_relative_export_path_handles_legacy_slashes_without_filesystem_access(self):
        from backend.server import relative_export_path
        for raw, expected in (
            (r"\\\\fixture-nas\\personal_folder\\Photos", "personal_folder/Photos"),
            (r"D:\\Photos\\2025", "Photos/2025"),
            ("/backup//Photos/./2025", "backup/Photos/2025"),
            ("smb://fixture-nas/personal_folder/Photos", "personal_folder/Photos"),
            ("smb://[server/share", "share"),
            ("file:///D:/Photos/2025", "Photos/2025"),
            ("../../Photos/../Study", "Study"),
            ("", ""),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(relative_export_path(raw), expected)

    def test_portable_export_preserves_deep_hierarchy_without_invalid_long_metadata(self):
        from backend.server import portable_export
        from backend.store import Store
        source = Store(self.root / "deep-source.sqlite3")
        parent = None
        for index in range(42):
            parent = source.create("assets", {"name": f"{index:03d}" + "x" * 197, "kind": "folder", "parent_id": parent["id"] if parent else None})
        source.update("assets", parent["id"], {"backup_target": True})
        version = source.create("versions", {"asset_id": parent["id"], "label": "kept"})
        location = source.create("locations", {"name": "旧位置", "kind": "nas", "base_path": "smb://[server/share"})
        source.create("records", {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-16T10:00:00Z", "notes": "保留深层记录"})
        before = source.state()
        exported = portable_export(source.export())
        self.assertLessEqual(max(len(asset["source_path"]) for asset in exported["assets"]), 8192)
        restored = Store(self.root / "deep-restored.sqlite3")
        self.assertEqual(restored.import_data(exported)["added"]["assets"], 42)
        self.assertEqual([(asset["id"], asset["parent_id"], asset["name"]) for asset in restored.state()["assets"]], [(asset["id"], asset["parent_id"], asset["name"]) for asset in before["assets"]])
        self.assertEqual(restored.state()["records"], before["records"])
        self.assertEqual(source.state(), before)

    def test_typed_locations_resolve_casefold_names_without_rewriting_existing_metadata(self):
        original = self.create("locations", {"name": "Straße", "kind": "nas", "base_path": "/keep", "notes": "preserve"})
        status, resolved, _ = self.request("POST", "/api/locations/resolve", {"name": " STRASSE "})
        self.assertEqual(status, 200)
        self.assertEqual(resolved, original)
        status, created, _ = self.request("POST", "/api/locations/resolve", {"name": "新硬盘"})
        self.assertEqual(status, 200)
        self.assertEqual(created["name"], "新硬盘")
        self.assertEqual(self.request("POST", "/api/locations/resolve", {"name": "新硬盘"})[1]["id"], created["id"])
        # Python casefold preserves dotless i, so these must stay separate.
        first = self.request("POST", "/api/locations/resolve", {"name": "I"})[1]
        second = self.request("POST", "/api/locations/resolve", {"name": "ı"})[1]
        self.assertNotEqual(first["id"], second["id"])

    def test_typed_location_resolution_rejects_invalid_inputs_without_writing(self):
        before = self.request("GET", "/api/state")[1]
        for payload in ({}, [], {"name": ""}, {"name": " "}, {"name": None}, {"name": "x" * 201}, {"name": "a\x00"}, {"name": "x", "kind": "cloud"}):
            self.assertEqual(self.request("POST", "/api/locations/resolve", payload)[0], 400)
        self.assertEqual(self.request("GET", "/api/state")[1], before)

    def test_concurrent_typed_locations_reuse_the_single_saved_location(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as workers:
            results = list(workers.map(lambda _: self.request("POST", "/api/locations/resolve", {"name": "同一硬盘"}), range(5)))
        self.assertTrue(all(result[0] == 200 for result in results), results)
        self.assertEqual(len({result[1]["id"] for result in results}), 1)
        self.assertEqual(len(self.request("GET", "/api/state")[1]["locations"]), 1)

    def test_first_record_http_marks_only_after_an_atomic_success(self):
        asset = self.create("assets", {"name": "首次备份", "kind": "file"})
        version = self.create("versions", {"asset_id": asset["id"], "label": "未标注"})
        location = self.create("locations", {"name": "硬盘", "kind": "external"})
        record = {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-15T10:00:00Z"}
        before = self.request("GET", "/api/state")[1]
        failed = {"records": [{**record, "backed_up_at": "invalid"}], "mark_asset_id": asset["id"]}
        self.assertEqual(self.request("POST", "/api/records/batch", failed)[0], 400)
        self.assertEqual(self.request("GET", "/api/state")[1], before)
        self.assertEqual(self.request("POST", "/api/records/batch", {"records": [record], "mark_asset_id": asset["id"]})[0], 201)
        after = self.request("GET", "/api/state")[1]
        self.assertTrue(after["assets"][0]["backup_target"])
        self.assertEqual(len(after["records"]), 1)

    def test_reorder_http_validates_complete_siblings_and_payload_atomically(self):
        a = self.create("assets", {"name": "A", "kind": "file"})
        b = self.create("assets", {"name": "B", "kind": "folder"})
        payload = {"parent_id": None, "ordered_ids": [b["id"], a["id"]]}
        self.assertEqual(self.request("POST", "/api/assets/reorder", payload)[:2], (200, {"ok": True}))
        before = self.request("GET", "/api/state")[1]
        self.assertEqual({x["id"]: x["manual_order"] for x in before["assets"]}, {a["id"]: 1, b["id"]: 0})
        for broken in ({"ordered_ids": []}, {"parent_id": None, "ordered_ids": [a["id"]]}, {**payload, "extra": True}, {"parent_id": "invalid", "ordered_ids": []}):
            self.assertIn(self.request("POST", "/api/assets/reorder", broken)[0], (400, 409))
            self.assertEqual(self.request("GET", "/api/state")[1], before)

    def test_health_state_and_missing_api_routes(self):
        self.assertEqual(self.request("GET", "/api/health")[:2], (200, {"app": "filebackup-ledger", "status": "ok"}))
        self.assertEqual(self.request("GET", "/api/state")[1], {"assets": [], "versions": [], "locations": [], "records": []})
        for path in ("/api/missing", "/api/", "/api/assets/missing"):
            status, body, _ = self.request("GET", path)
            self.assertEqual(status, 404)
            self.assertIn("error", body)

    def test_crud_batch_export_and_additive_import_routes(self):
        asset = self.create("assets", {"name": "资料", "kind": "file", "backup_target": True})
        version = self.create("versions", {"asset_id": asset["id"], "label": "v1"})
        first = self.create("locations", {"name": "硬盘", "kind": "external"})
        second = self.create("locations", {"name": "NAS", "kind": "nas"})
        records = [{"version_id": version["id"], "location_id": location["id"], "backed_up_at": when} for location, when in ((first, "2026-10-09T10:00:00+08:00"), (second, "2026-10-10T11:00:00+08:00"))]
        status, batch, _ = self.request("POST", "/api/records/batch", {"records": records})
        self.assertEqual(status, 201)
        self.assertEqual(len(batch["records"]), 2)
        record = batch["records"][0]
        self.assertEqual(self.request("PUT", f"/api/records/{record['id']}", {"notes": "已校验"})[1]["notes"], "已校验")
        self.assertEqual(self.request("DELETE", f"/api/assets/{asset['id']}")[0], 409)
        exported = self.request("GET", "/api/export")[1]
        self.assertEqual(self.request("POST", "/api/import", exported)[:2], (200, {"added": {"assets": 0, "versions": 0, "locations": 0, "records": 0}}))
        status, removed, _ = self.request("DELETE", f"/api/records/{record['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(removed['deleted'])
        self.assertIsInstance(removed['trash_id'], str)
        self.assertEqual(self.request("DELETE", f"/api/records/{record['id']}")[0], 404)

    def test_invalid_payloads_return_json_and_do_not_mutate(self):
        for payload in ([], {"name": "资料", "kind": "file", "id": "x"}, {"name": "资料"}):
            status, body, _ = self.request("POST", "/api/assets", payload)
            self.assertEqual(status, 400)
            self.assertIn("error", body)
        for raw in (b"broken", b'{"name": "x", "kind": NaN}', b'{"name":"x","name":"y","kind":"file"}'):
            self.assertEqual(self.request("POST", "/api/assets", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/api/records/batch", {"records": [], "hidden": True})[0], 400)
        self.assertEqual(self.request("GET", "/api/state")[1]["assets"], [])

    def test_hierarchy_policy_json_fields_and_conflicts_reach_generic_routes(self):
        parent = self.create("assets", {"name": "目录", "kind": "folder"})
        location = self.create("locations", {"name": "政策目标", "kind": "cloud"})
        self.assertIsNone(parent["parent_id"])
        self.assertEqual(parent["backup_interval_days"], 30)
        self.assertEqual(parent["target_location_ids"], [])
        self.assertIs(parent.get("backup_enabled"), True)
        child = self.create("assets", {"name": "叶节点", "kind": "file", "parent_id": parent["id"],
                                       "backup_interval_days": 7, "target_location_ids": [location["id"]], "backup_enabled": False})
        self.assertEqual(child["parent_id"], parent["id"])
        self.assertEqual(child["backup_interval_days"], 7)
        self.assertEqual(child["target_location_ids"], [location["id"]])
        self.assertIs(child["backup_enabled"], False)
        self.assertEqual(self.request("DELETE", f"/api/assets/{parent['id']}")[0], 409)
        self.assertEqual(self.request("DELETE", f"/api/locations/{location['id']}")[0], 409)
        self.assertEqual(self.request("PUT", f"/api/assets/{child['id']}", {"backup_interval_days": True})[0], 400)
        self.assertEqual(self.request("PUT", f"/api/assets/{child['id']}", {"backup_enabled": 0})[0], 400)
        version = self.create("versions", {"asset_id": parent["id"], "label": "历史目录版本"})
        self.assertEqual(self.request("POST", "/api/records", {"version_id": version["id"], "location_id": location["id"],
                                                              "backed_up_at": "2026-10-10T10:00:00Z"})[0], 409)
        exported = self.request("GET", "/api/export")[1]
        self.assertEqual(exported["schema_version"], 3)
        self.assertEqual(exported["assets"][1], {**child, "source_path": "目录/叶节点"})

    def test_explicit_target_default_validation_and_record_conflicts_reach_api(self):
        ordinary = self.create("assets", {"name": "普通叶文件", "kind": "file"})
        self.assertIs(ordinary.get("backup_target"), False)
        version = self.create("versions", {"asset_id": ordinary["id"], "label": "v1"})
        location = self.create("locations", {"name": "手动位置", "kind": "external"})
        payload = {"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-11T08:00:00Z"}
        status, error, _ = self.request("POST", "/api/records", payload)
        self.assertEqual(status, 409)
        self.assertIn("设为备份对象", error["error"])
        self.assertEqual(self.request("PUT", f"/api/assets/{ordinary['id']}", {"backup_target": 1})[0], 400)
        status, marked, _ = self.request("PUT", f"/api/assets/{ordinary['id']}", {"backup_target": True, "backup_enabled": False})
        self.assertEqual(status, 200)
        self.assertIs(marked["backup_target"], True)
        self.assertEqual(self.request("POST", "/api/records", payload)[0], 201)
        exported = self.request("GET", "/api/export")[1]
        self.assertEqual(exported["schema_version"], 3)
        self.assertIs(exported["assets"][0]["backup_target"], True)

    def test_mixed_marked_and_ordinary_record_batch_returns409_with_no_partial_records(self):
        marked = self.create("assets", {"name": "备份目标", "kind": "file", "backup_target": True})
        ordinary = self.create("assets", {"name": "普通文件", "kind": "file"})
        location = self.create("locations", {"name": "批量位置", "kind": "external"})
        versions = [self.create("versions", {"asset_id": asset["id"], "label": "v1"}) for asset in (marked, ordinary)]
        records = [{"version_id": version["id"], "location_id": location["id"], "backed_up_at": "2026-10-11T08:00:00Z"} for version in versions]
        before = self.request("GET", "/api/state")[1]
        self.assertEqual(self.request("POST", "/api/records/batch", {"records": records})[0], 409)
        self.assertEqual(self.request("GET", "/api/state")[1], before)

    def test_host_origin_and_json_security(self):
        status, _, headers = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for host in ("evil.test", "127.0.0.1:1", "localhost.evil.test:" + str(self.port), "127.0.0.1:" + str(self.port) + ".evil"):
            self.assertEqual(self.request("GET", "/api/health", headers={"Host": host})[0], 403)
        for origin in ("https://evil.test", "null", "http://127.0.0.1:1", "http://localhost:" + str(self.port)):
            self.assertEqual(self.request("POST", "/api/assets", {"name": "x", "kind": "file"}, headers={"Origin": origin})[0], 403)
        local_origin = "http://127.0.0.1:" + str(self.port)
        self.assertEqual(self.request("POST", "/api/assets", {"name": "资料", "kind": "file"}, headers={"Origin": local_origin})[0], 201)
        self.assertEqual(self.request("POST", "/api/assets", raw=b'{}', headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/assets", raw=b'{}', headers={"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.request("OPTIONS", "/api/assets", headers={"Origin": "https://evil.test"})[0], 403)

    def test_large_body_rejected_before_reading(self):
        status, body, _ = self.request("POST", "/api/import", raw=b"", headers={"Content-Length": str(MAX_BODY_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertIn("error", body)
        status, body, _ = self.request("POST", "/api/import", raw=b"", headers={"Content-Length": "9" * 5000})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_early_origin_rejection_delivers_response_with_pending_post_body(self):
        for _ in range(30):
            status, body, _ = self.request("POST", "/api/assets", {"name": "资料", "kind": "file"}, headers={"Origin": "https://evil.test"})
            self.assertEqual(status, 403)
            self.assertIn("error", body)

    def test_static_files_and_paths_are_confined_to_dist(self):
        self.assertIn("本地台账", self.request("GET", "/")[1])
        status, script, headers = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("ledger", script)
        self.assertIn("javascript", headers["Content-Type"])
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/..%5cprivate.txt", "/ledger.sqlite3", "/backend/store.py", "/app.py", "/app.js/../../private.txt"):
            status, body, _ = self.request("GET", path)
            self.assertIn(status, (403, 404), path)
            self.assertNotIn("must stay private", str(body))
        self.assertEqual(self.request("HEAD", "/")[0], 200)

    def test_directory_read_endpoint_is_removed_and_preserves_state(self):
        before = self.request("GET", "/api/state")[1]
        status, result, _ = self.request("POST", "/api/inventory", {})
        self.assertEqual(status, 404)
        self.assertIn("error", result)
        self.assertEqual(self.request("GET", "/api/state")[1], before)

    def test_manual_file_and_folder_paths_need_no_filesystem_access(self):
        paths = (
            ("文件", "file", r"\\offline-nas.invalid\资料\论文.pdf"),
            ("文件夹", "folder", r"Z:\尚未挂载\研究资料"),
        )
        real_scandir = os.scandir
        def owned_snapshot_only(directory):
            self.assertEqual(Path(directory).resolve(), self.server.snapshots.directory,
                             'manual registration must not list source directories')
            return real_scandir(directory)
        with patch("os.scandir", side_effect=owned_snapshot_only):
            for name, kind, path in paths:
                asset = self.create("assets", {"name": name, "kind": kind, "source_path": path})
                self.assertEqual(asset["source_path"], path)
                self.assertEqual(asset["kind"], kind)
            state = self.request("GET", "/api/state")[1]
            self.assertEqual(len(state["assets"]), 2)

    def test_absolute_entry_runs_from_other_cwd_and_reports_bind_failure(self):
        app = Path(__file__).resolve().parent.parent / "app.py"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        command = [sys.executable, str(app), "--port", str(port), "--db", str(self.root / "cli.sqlite3")]
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        try:
            healthy = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process.poll() is None:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                try:
                    connection.request("GET", "/api/health")
                    response = connection.getresponse()
                    healthy = response.status == 200 and json.loads(response.read())["app"] == "filebackup-ledger"
                    if healthy:
                        break
                except OSError:
                    time.sleep(0.03)
                finally:
                    connection.close()
            self.assertTrue(healthy, "absolute app.py did not start from the alternate directory")
            self.assertTrue((self.root / "cli.sqlite3").is_file())
            conflict = subprocess.run(command, cwd=self.root, capture_output=True, timeout=5, env=environment)
            self.assertEqual(conflict.returncode, 1)
            message = conflict.stderr.decode("utf-8")
            self.assertIn("启动失败", message)
            self.assertIn(str(port), message)
            self.assertNotIn("Traceback", message)
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_startup_and_bind_error_survive_strict_cp1252_redirected_streams(self):
        app = Path(__file__).resolve().parent.parent / "app.py"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        command = [sys.executable, str(app), "--port", str(port), "--db", str(self.root / "encoding.sqlite3")]
        environment = {**os.environ, "PYTHONIOENCODING": "cp1252:strict"}
        process = subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        try:
            healthy = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process.poll() is None:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                try:
                    connection.request("GET", "/api/health")
                    response = connection.getresponse()
                    healthy = response.status == 200 and json.loads(response.read())["app"] == "filebackup-ledger"
                    if healthy:
                        break
                except OSError:
                    time.sleep(0.03)
                finally:
                    connection.close()
            if not healthy:
                stdout, stderr = process.communicate(timeout=5)
                self.fail("startup failed with cp1252:strict: " + stderr.decode("utf-8", errors="replace"))
            conflict = subprocess.run(command, cwd=self.root, capture_output=True, timeout=5, env=environment)
            self.assertEqual(conflict.returncode, 1)
            self.assertIn("启动失败", conflict.stderr.decode("utf-8"))
            self.assertNotIn(b"UnicodeEncodeError", conflict.stderr)
        finally:
            if process.poll() is None:
                process.terminate()
            stdout, stderr = process.communicate(timeout=5)
        self.assertIn("备份台账已启动", stdout.decode("utf-8"))
        self.assertNotIn(b"UnicodeEncodeError", stderr)


if __name__ == "__main__":
    unittest.main()
