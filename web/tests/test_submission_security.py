import importlib
import unittest
import http.client
import json
import tempfile
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from backend.store import StoreError

try:
    security = importlib.import_module("backend.security")
except ModuleNotFoundError:
    security = None


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class SubmissionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(security, "submission guard is missing")
        self.clock = Clock()

    def guard(self, peer=((3, 10),), global_rules=((5, 60),), max_clients=10):
        return security.SubmissionGuard(clock=self.clock, peer_rules={"write": peer},
                                        global_rules={"write": global_rules}, max_clients=max_clients)

    def test_weighted_budgets_and_retry_seconds_recover_at_boundary(self):
        guard = self.guard()
        guard.consume("write", "peer", cost=2)
        with self.assertRaises(StoreError) as caught:
            guard.consume("write", "peer", cost=2)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 10)
        self.clock.now += 10
        guard.consume("write", "peer", cost=2)

    def test_rejected_attempts_do_not_extend_or_consume_the_budget(self):
        guard = self.guard()
        guard.consume("write", "peer", cost=3)
        self.clock.now += 4
        for _ in range(3):
            with self.assertRaises(StoreError) as caught:
                guard.consume("write", "peer")
            self.assertEqual(caught.exception.retry_after, 6)
        self.clock.now += 6
        guard.consume("write", "peer", cost=2)

    def test_global_budget_survives_bounded_client_eviction(self):
        guard = self.guard(max_clients=2)
        for index in range(5):
            guard.consume("write", str(index))
        self.assertLessEqual(len(guard._clients), 2)
        with self.assertRaises(StoreError) as caught:
            guard.consume("write", "rotated")
        self.assertEqual(caught.exception.status, 429)

    def test_concurrent_accounting_cannot_exceed_budget(self):
        guard = self.guard()
        def submit(_):
            try:
                guard.consume("write", "peer")
                return 200
            except StoreError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(submit, range(20)))
        self.assertEqual(results.count(200), 3)
        self.assertEqual(results.count(429), 17)

    def test_notes_limit_preserves_identical_legacy_text_but_rejects_new_long_text(self):
        old = "旧" * 3000
        security.validate_submission_notes({"notes": old}, previous_notes=old)
        security.validate_submission_notes({"notes": "文" * 2000})
        for value in ("文" * 2001, old + "变"):
            with self.assertRaises(StoreError):
                security.validate_submission_notes({"notes": value}, previous_notes=old)

    def test_notes_are_plain_text_and_invalid_unicode_is_rejected(self):
        security.validate_submission_notes({"notes": '<img src=x onerror="alert(1)">\n<script>text</script>'})
        with self.assertRaises(StoreError):
            security.validate_submission_notes({"notes": "\ud800"})


class SubmissionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(security, "submission guard is missing")
        from backend.server import make_server
        from backend.config import ServerConfig
        from backend.storage import SQLiteStore
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = SQLiteStore(Path(self.temp.name) / "ledger.sqlite3")
        guard = security.SubmissionGuard(clock=self.clock)
        self.server = make_server(config=ServerConfig(port=0), store=self.store, submission_guard=guard)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.asset = self.store.create("assets", {"name": "资料", "kind": "file", "backup_target": True})
        self.version = self.store.create("versions", {"asset_id": self.asset["id"], "label": "未标注"})
        self.location = self.store.create("locations", {"name": "NAS", "kind": "nas"})
        self.payload = {"version_id": self.version["id"], "location_id": self.location["id"], "backed_up_at": "2026-10-17T08:00:00Z", "notes": "正常备注"}

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        values = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        values.update(headers or {})
        connection.request(method, path, body=json.dumps(payload).encode() if payload is not None else None, headers=values)
        response = connection.getresponse(); data = response.read()
        result = response.status, json.loads(data) if "application/json" in response.getheader("Content-Type", "") else data.decode(), dict(response.getheaders())
        connection.close(); return result

    def test_notes_and_batch_bounds_are_enforced_before_any_write(self):
        before = self.store.state()
        self.assertEqual(self.request("POST", "/api/records", {**self.payload, "notes": "文" * 2001})[0], 400)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.request("POST", "/api/records/batch", {"records": [self.payload] * 21})[0], 413)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.request("POST", "/api/records/batch", {"records": [self.payload] * 20})[0], 201)
        status, body, headers = self.request("POST", "/api/records", self.payload)
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], str(body["retry_after"]))
        self.assertEqual(len(self.store.state()["records"]), 20)

    def test_legacy_notes_can_be_retained_or_shortened_and_import_remains_compatible(self):
        record = self.store.create("records", {**self.payload, "notes": "旧" * 3000})
        path = "/api/records/" + record["id"]
        self.assertEqual(self.request("PUT", path, {"notes": record["notes"], "backed_up_at": "2026-10-18T08:00:00Z"})[0], 200)
        self.assertEqual(self.request("PUT", path, {"notes": record["notes"] + "变"})[0], 400)
        self.assertEqual(self.request("POST", "/api/import", self.store.export())[0], 200)
        self.assertEqual(self.request("PUT", path, {"notes": "文" * 2000})[0], 200)

    def test_write_throttle_ignores_forwarded_addresses_and_does_not_block_reads(self):
        self.server.submissions = security.SubmissionGuard(clock=self.clock, peer_rules={**security.PEER_RULES, "write": ((2, 10),)})
        for index in range(2):
            self.assertEqual(self.request("POST", "/api/assets", {"name": str(index), "kind": "file"}, {"X-Forwarded-For": str(index)})[0], 201)
        status, body, headers = self.request("POST", "/api/assets", {"name": "blocked", "kind": "file"}, {"X-Forwarded-For": "new"})
        self.assertEqual(status, 429); self.assertEqual(body["retry_after"], 10); self.assertEqual(headers["Retry-After"], "10")
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.clock.now += 10
        self.assertEqual(self.request("POST", "/api/assets", {"name": "later", "kind": "file"})[0], 201)

    def test_login_admission_counts_malformed_requests_and_recovers(self):
        for index in range(10):
            self.assertEqual(self.request("POST", "/api/login", {"password": {}}, {"X-Forwarded-For": str(index)})[0], 400)
        status, body, headers = self.request("POST", "/api/login", {"password": "valid-local"})
        self.assertEqual(status, 429); self.assertEqual(headers["Retry-After"], "60")
        self.assertEqual(body["retry_after"], 60)
        self.clock.now += 60
        self.assertEqual(self.request("POST", "/api/login", {"password": "valid-local"})[0], 200)

    def test_request_size_import_frequency_and_script_headers(self):
        self.assertEqual(self.request("POST", "/api/assets", {"name": "x" * (security.MAX_WRITE_BYTES + 1), "kind": "file"})[0], 413)
        exported = self.store.export()
        self.assertEqual(self.request("POST", "/api/import", exported)[0], 200)
        self.assertEqual(self.request("POST", "/api/import", exported)[0], 200)
        self.assertEqual(self.request("POST", "/api/import", exported)[0], 429)
        _, _, headers = self.request("GET", "/api/state")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
