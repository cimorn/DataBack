"""NAS HTTP boundary contracts; all ledger state here belongs to temporary stores."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from backend.server import make_server
from backend.storage import create_store
from backend.store import StoreError


ORIGIN = "https://backup.example.test"
PASSWORD = "Test-only-admin!7x9V4qP2"
SECRET = "test-only-session-secret-f45c80ed837b421792068"


def nas_environment(**changes):
    values = {
        "DATABACK_HOST": "0.0.0.0",
        "DATABACK_PORT": "8767",
        "DATABACK_PUBLIC_ORIGIN": ORIGIN,
        "DATABACK_ALLOWED_HOSTS": "backup.example.test,127.0.0.1:8767,localhost:8767,192.0.2.10:8093",
        "DATABACK_ADMIN_PASSWORD": PASSWORD,
        "DATABACK_SESSION_SECRET": SECRET,
        "DATABACK_PRIVACY_PASSWORD": "fixture-privacy",
    }
    values.update(changes)
    return values


class ConfigTests(unittest.TestCase):
    def config_type(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.config"), "explicit server configuration is missing")
        from backend.config import ServerConfig
        return ServerConfig

    def test_local_defaults_and_nas_environment(self):
        config_type = self.config_type()
        local = config_type.from_env({})
        self.assertEqual((local.host, local.port, local.mongo_database), ("127.0.0.1", 8767, "databack"))
        self.assertFalse(local.auth_required)
        config = config_type.from_env(nas_environment(MONGODB_URI="mongodb://user:private-uri-password@mongo/"))
        self.assertTrue(config.auth_required)
        self.assertEqual(config.public_origin, ORIGIN)
        self.assertEqual(config.allowed_hosts, frozenset({"backup.example.test", "127.0.0.1:8767", "localhost:8767", "192.0.2.10:8093"}))
        for secret in (PASSWORD, SECRET, "private-uri-password"):
            self.assertNotIn(secret, repr(config))

    def test_nonloopback_requires_complete_strong_configuration(self):
        config_type = self.config_type()
        invalid = [
            {"DATABACK_HOST": "0.0.0.0"},
            nas_environment(DATABACK_ADMIN_PASSWORD="short"),
            nas_environment(DATABACK_ADMIN_PASSWORD="x" * 32),
            nas_environment(DATABACK_SESSION_SECRET="short"),
            nas_environment(DATABACK_SESSION_SECRET="x" * 64),
            nas_environment(DATABACK_ALLOWED_HOSTS="*"),
            nas_environment(DATABACK_ALLOWED_HOSTS="evil.test"),
            nas_environment(DATABACK_PUBLIC_ORIGIN="http://backup.example.test"),
            nas_environment(DATABACK_PUBLIC_ORIGIN=ORIGIN + "/path"),
            nas_environment(DATABACK_PUBLIC_ORIGIN=ORIGIN + "/"),
            nas_environment(DATABACK_PUBLIC_ORIGIN="https://user:secret@backup.example.test"),
            nas_environment(DATABACK_ALLOWED_HOSTS="backup.example.test,"),
            nas_environment(DATABACK_PORT="bad-private-value"),
            nas_environment(MONGODB_URI=""),
            nas_environment(MONGODB_DATABASE="bad/name"),
        ]
        for values in invalid:
            with self.subTest(keys=sorted(values)):
                with self.assertRaises(ValueError) as caught:
                    config_type.from_env(values)
                for secret in (PASSWORD, SECRET, "bad-private-value", "user:secret"):
                    self.assertNotIn(secret, str(caught.exception))

    def test_short_admin_password_requires_explicit_opt_in(self):
        config_type = self.config_type()
        self.assertFalse(config_type.from_env({}).allow_short_password)
        for flag in (None, "false"):
            values = nas_environment(DATABACK_ADMIN_PASSWORD="pin")
            if flag is not None:
                values["DATABACK_ALLOW_SHORT_PASSWORD"] = flag
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                config_type.from_env(values)
        config = config_type.from_env(nas_environment(
            DATABACK_ADMIN_PASSWORD="pin", DATABACK_ALLOW_SHORT_PASSWORD="true"))
        self.assertTrue(config.auth_required)
        self.assertTrue(config.allow_short_password)
        self.assertEqual(config.admin_password, "pin")

    def test_short_password_flag_accepts_only_literal_true_or_false(self):
        config_type = self.config_type()
        self.assertFalse(config_type.from_env(nas_environment(DATABACK_ALLOW_SHORT_PASSWORD="false")).allow_short_password)
        for flag in ("", "1", "0", "yes", "no", "TRUE", "False", " true", "false ", None, True):
            with self.subTest(flag=flag), self.assertRaises(ValueError) as caught:
                config_type.from_env(nas_environment(DATABACK_ALLOW_SHORT_PASSWORD=flag))
            self.assertIn("DATABACK_ALLOW_SHORT_PASSWORD", str(caught.exception))

    def test_short_password_opt_in_preserves_password_bounds(self):
        config_type = self.config_type()
        for password in (None, "", "pin\n", "pin\x00", "pin\x7f", "x" * 1025):
            with self.subTest(length=len(password) if password is not None else None), self.assertRaises(ValueError):
                config_type.from_env(nas_environment(
                    DATABACK_ADMIN_PASSWORD=password, DATABACK_ALLOW_SHORT_PASSWORD="true"))
        for password in ("x", "x" * 1024, "\U0001f511" * 1024):
            with self.subTest(length=len(password)):
                self.assertEqual(config_type.from_env(nas_environment(
                    DATABACK_ADMIN_PASSWORD=password, DATABACK_ALLOW_SHORT_PASSWORD="true")).admin_password, password)

    def test_short_password_opt_in_still_requires_strong_session_secret(self):
        config_type = self.config_type()
        for secret in (None, "", "short", "x" * 64, SECRET + "\n"):
            with self.subTest(length=len(secret) if secret is not None else None), self.assertRaises(ValueError) as caught:
                config_type.from_env(nas_environment(
                    DATABACK_ADMIN_PASSWORD="pin", DATABACK_ALLOW_SHORT_PASSWORD="true",
                    DATABACK_SESSION_SECRET=secret))
            self.assertIn("DATABACK_SESSION_SECRET", str(caught.exception))

    def test_short_password_opt_in_still_requires_https_origin_and_exact_hosts(self):
        config_type = self.config_type()
        for changes in ({"DATABACK_PUBLIC_ORIGIN": "http://backup.example.test"},
                        {"DATABACK_ALLOWED_HOSTS": "*"}, {"DATABACK_ALLOWED_HOSTS": "evil.test"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config_type.from_env(nas_environment(
                    DATABACK_ADMIN_PASSWORD="pin", DATABACK_ALLOW_SHORT_PASSWORD="true", **changes))

    def test_direct_short_password_opt_in_requires_boolean(self):
        config_type = self.config_type()
        for flag in ("true", "false", 1, 0, None):
            with self.subTest(flag=flag), self.assertRaises(ValueError) as caught:
                config_type(allow_short_password=flag)
            self.assertIn("DATABACK_ALLOW_SHORT_PASSWORD", str(caught.exception))

    def test_nas_invalid_startup_never_falls_back_or_prints_secrets(self):
        self.config_type()
        from backend.server import main
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / "must-not-exist.sqlite3"
            environment = nas_environment(DATABACK_ADMIN_PASSWORD="private-short-password", DATABACK_SESSION_SECRET="private-short-secret")
            with patch.dict(os.environ, environment, clear=True), patch("backend.server.create_store") as factory:
                with contextlib.redirect_stderr(io.StringIO()) as errors, contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--db", str(db_path)]), 1)
                factory.assert_not_called()
                message = errors.getvalue() + output.getvalue()
                self.assertIn("启动失败", message)
                for secret in ("private-short-password", "private-short-secret", "Traceback"):
                    self.assertNotIn(secret, message)
            self.assertFalse(db_path.exists())

    def test_environment_port_and_mongo_are_used_and_cli_port_takes_precedence(self):
        config_type = self.config_type()
        self.assertEqual(config_type.from_env({"DATABACK_PORT": "8093"}).port, 8093)
        self.assertEqual(config_type.from_env({"DATABACK_PORT": "8093"}, port=8767).port, 8767)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"MONGODB_URI": "mongodb://private-user:private-password@private-host:bad-port/"}, clear=True):
            path = Path(folder) / "must-not-exist.sqlite3"
            with self.assertRaises(StoreError) as caught:
                make_server(port=0, db_path=path)
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn("private-", str(caught.exception))
            self.assertFalse(path.exists())


class Clock:
    def __init__(self):
        self.now = 1800000000.0

    def __call__(self):
        return self.now


class NasServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<title>databack</title>", encoding="utf-8")
        # Assert missing modules as feature failures rather than import errors.
        self.assertIsNotNone(importlib.util.find_spec("backend.config"), "NAS configuration is missing")
        self.assertIsNotNone(importlib.util.find_spec("backend.auth"), "NAS authentication is missing")
        from backend.auth import SessionAuth
        from backend.config import ServerConfig
        from backend.security import SubmissionGuard
        self.config = ServerConfig.from_env(nas_environment())
        self.clock = Clock()
        self.auth = SessionAuth(self.config.admin_password, self.config.session_secret, clock=self.clock)
        self.store = create_store(self.root / "ledger.sqlite3")
        self.server = make_server(port=0, static_dir=self.dist, config=self.config, store=self.store, auth=self.auth, submission_guard=SubmissionGuard(clock=self.clock))
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None, duplicate_headers=None):
        values = {"Host": "backup.example.test"}
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            values.update({"Origin": ORIGIN, "Content-Type": "application/json"})
        values.update(headers or {})
        values = {key: value for key, value in values.items() if value is not None}
        body = json.dumps(payload).encode() if payload is not None else raw
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        if duplicate_headers:
            connection.putrequest(method, path, skip_host=True)
            for key, value in list(values.items()) + duplicate_headers:
                connection.putheader(key, value)
            if body is not None:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
        else:
            connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, json.loads(data) if data and "application/json" in response.getheader("Content-Type", "") else data,
                  dict(response.getheaders()))
        connection.close()
        return result

    def login(self):
        status, body, headers = self.request("POST", "/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200, body)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_reorder_requires_session_and_exact_origin_and_preserves_state_on_rejection(self):
        a = self.store.create("assets", {"name": "A", "kind": "file"})
        b = self.store.create("assets", {"name": "B", "kind": "folder"})
        payload = {"parent_id": None, "ordered_ids": [b["id"], a["id"]]}
        before = self.store.state()
        self.assertEqual(self.request("POST", "/api/assets/reorder", payload)[0], 401)
        cookie = self.login()
        for origin in (None, "https://wrong.example"):
            self.assertEqual(self.request("POST", "/api/assets/reorder", payload, headers={"Cookie": cookie, "Origin": origin})[0], 403)
            self.assertEqual(self.store.state(), before)
        self.assertEqual(self.request("POST", "/api/assets/reorder", payload, headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.store.state()["assets"][0]["manual_order"], 1)

    def test_public_session_health_and_static_do_not_expose_ledger(self):
        self.assertEqual(self.request("GET", "/api/session")[:2], (200, {"authenticated": False, "auth_required": True, "storage": "sqlite"}))
        status, body, headers = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"app": "filebackup-ledger", "product": "databack", "status": "ok", "storage": "sqlite"})
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("GET", "/")[0], 200)
        for host in ("127.0.0.1:8767", "localhost:8767", "192.0.2.10:8093"):
            self.assertEqual(self.request("GET", "/api/health", headers={"Host": host})[0], 200)
            self.assertEqual(self.request("GET", "/api/state", headers={"Host": host})[0], 401)

    def test_every_private_method_requires_cookie_before_payload_processing(self):
        routes = [("GET", "/api/state"), ("GET", "/api/export"), ("HEAD", "/api/state"),
                  ("OPTIONS", "/api/export"), ("POST", "/api/assets"), ("POST", "/api/versions"),
                  ("POST", "/api/locations"), ("POST", "/api/records"), ("POST", "/api/import"),
                  ("POST", "/api/records/batch"), ("POST", "/api/assets/reorder"), ("PUT", "/api/assets/id"), ("DELETE", "/api/records/id")]
        for method, path in routes:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, raw=b"broken")[0], 401)
        self.assertEqual(self.store.state()["assets"], [])

    def test_login_cookie_authenticated_crud_and_logout_revocation(self):
        status, body, headers = self.request("POST", "/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"authenticated": True, "auth_required": True, "storage": "sqlite"})
        cookie_header = headers["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/", "Max-Age=43200"):
            self.assertIn(flag, cookie_header)
        cookie = cookie_header.split(";", 1)[0]
        self.assertNotIn(PASSWORD, cookie)
        signed = {"Cookie": cookie}
        self.assertTrue(self.request("GET", "/api/session", headers=signed)[1]["authenticated"])
        status, asset, _ = self.request("POST", "/api/assets", {"name": "manual", "kind": "file", "source_path": "//offline/folder"}, headers=signed)
        self.assertEqual(status, 201)
        self.assertEqual(self.request("GET", "/api/export", headers=signed)[1]["assets"][0], {**asset, "source_path": asset["name"]})
        self.assertEqual(self.request("PUT", "/api/assets/" + asset["id"], {"notes": "verified"}, headers=signed)[0], 200)
        self.assertEqual(self.request("DELETE", "/api/assets/" + asset["id"], headers=signed)[0], 200)
        status, result, logout_headers = self.request("POST", "/api/logout", {}, headers=signed)
        self.assertEqual(status, 200)
        self.assertFalse(result["authenticated"])
        self.assertIn("Max-Age=0", logout_headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/api/state", headers=signed)[0], 401)
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)

    def test_session_expiry_tampering_and_duplicate_cookies(self):
        cookie = self.login()
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie + "x"})[0], 401)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie + "; " + cookie})[0], 401)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie}, duplicate_headers=[("Cookie", cookie)])[0], 401)
        self.clock.now += 43201
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie})[0], 401)
        self.assertFalse(self.request("GET", "/api/session", headers={"Cookie": cookie})[1]["authenticated"])

    def test_origins_required_exact_and_headers_not_inferred_from_proxy(self):
        cookie = self.login()
        for origin in (None, "null", "http://backup.example.test", ORIGIN + "/", ORIGIN + ".evil", "https://evil.test"):
            for path, payload in (("/api/login", {"password": PASSWORD}), ("/api/logout", {}), ("/api/assets", {"name": "x", "kind": "file"})):
                with self.subTest(path=path, origin=origin):
                    self.assertEqual(self.request("POST", path, payload, headers={"Origin": origin, "Cookie": cookie})[0], 403)
        self.assertEqual(self.request("POST", "/api/assets", {"name": "x", "kind": "file"}, headers={"Cookie": cookie}, duplicate_headers=[("Origin", ORIGIN)])[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"password": PASSWORD}, headers={"Origin": None, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "backup.example.test"})[0], 403)
        for host in ("backup.example.test:443", "backup.example.test.evil", "evil.test", "192.0.2.10:8092"):
            self.assertEqual(self.request("GET", "/api/health", headers={"Host": host})[0], 403)
        self.assertEqual(self.request("GET", "/api/health", duplicate_headers=[("Host", "backup.example.test")])[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Cookie": cookie, "Origin": "https://evil.test"})[0], 403)

    def test_raw_get_targets_reject_network_paths_and_fragments(self):
        for target in ("//api/health", "/api/health#fragment"):
            with self.subTest(target=target):
                self.assertEqual(self.request("GET", target)[0], 400)

    def test_raw_authenticated_post_targets_reject_without_creating_entries(self):
        cookie = self.login()
        for index, target in enumerate(("//api/assets", "/api/assets#fragment")):
            with self.subTest(target=target):
                self.assertEqual(self.request("POST", target, {"name": "invalid raw target " + str(index), "kind": "file"}, headers={"Cookie": cookie})[0], 400)
        self.assertEqual(self.store.state()["assets"], [])

    def test_malformed_login_and_logout_are_bounded_and_never_echo_secrets(self):
        for payload in ([], {}, {"password": PASSWORD, "other": True}, {"password": 1}, {"password": ""}, {"password": "x" * 1025}):
            self.assertEqual(self.request("POST", "/api/login", payload)[0], 400)
        for raw in (b"broken", b'{"password":"x","password":"y"}', b'{"password":NaN}'):
            self.assertEqual(self.request("POST", "/api/login", raw=raw)[0], 400)
        self.assertEqual(self.request("POST", "/api/login", raw=b'{}', headers={"Content-Type": "text/plain"})[0], 415)
        self.clock.now += 60  # This test checks framing after ten deliberate malformed attempts.
        self.assertEqual(self.request("POST", "/api/login", raw=b"", headers={"Content-Length": "8193"})[0], 413)
        self.assertEqual(self.request("POST", "/api/logout", {"password": PASSWORD})[0], 400)
        status, body, _ = self.request("POST", "/api/login?password=" + PASSWORD, {"password": "wrong"})
        self.assertEqual(status, 400)
        self.assertNotIn(PASSWORD, str(body))
        self.assertEqual(self.request("HEAD", "/api/login")[0], 405)
        self.assertEqual(self.request("OPTIONS", "/api/login")[0], 405)

    def test_unicode_malformed_password_is_validation_failure(self):
        self.assertEqual(self.request("POST", "/api/login", raw=b'{"password":"\\ud800"}')[0], 400)

    def test_authenticated_method_and_framing_checks_cannot_mutate(self):
        cookie = self.login()
        for method in ("HEAD", "OPTIONS", "PATCH"):
            self.assertIn(self.request(method, "/api/assets", {}, headers={"Cookie": cookie})[0], (404, 405))
        for duplicate in (("Content-Type", "application/json"), ("Content-Length", "2")):
            self.assertIn(self.request("POST", "/api/assets", {}, headers={"Cookie": cookie}, duplicate_headers=[duplicate])[0], (400, 415))
        self.assertEqual(self.request("POST", "/api/assets", {}, headers={"Cookie": cookie, "Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.store.state()["assets"], [])

    def test_login_failure_rate_limit_ignores_forwarded_ips_and_recovers(self):
        for index in range(5):
            status, body, _ = self.request("POST", "/api/login", {"password": "wrong"}, headers={"X-Forwarded-For": "203.0.113." + str(index)})
            self.assertEqual(status, 401)
            self.assertNotIn("wrong", str(body))
        status, body, headers = self.request("POST", "/api/login", {"password": PASSWORD})
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "300")
        self.assertEqual(body["retry_after"], 300)
        self.clock.now += 301
        self.assertEqual(self.request("POST", "/api/login", {"password": PASSWORD})[0], 200)

    def test_readiness_failure_and_unexpected_storage_errors_are_sanitized(self):
        secret = "mongodb://private-user:private-password@private-host/"
        with patch.object(self.store, "readiness", side_effect=StoreError(secret, 503)):
            status, body, _ = self.request("GET", "/api/health")
            self.assertEqual(status, 503)
            self.assertNotIn("private-", str(body))
        with patch.object(self.store, "readiness", return_value=False):
            self.assertEqual(self.request("GET", "/api/health")[0], 503)
        cookie = self.login()
        with patch.object(self.store, "state", side_effect=RuntimeError(secret)), contextlib.redirect_stderr(io.StringIO()) as captured:
            status, body, _ = self.request("GET", "/api/state", headers={"Cookie": cookie})
            self.assertEqual(status, 500)
            self.assertNotIn("private-", str(body) + captured.getvalue())

    def test_server_close_and_bind_failure_release_store(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        with patch.object(self.store, "close", wraps=self.store.close) as close:
            self.server.server_close()
            close.assert_called_once()
        with socket.socket() as reservation:
            reservation.bind(("0.0.0.0", 0))
            reservation.listen()
            with patch.object(self.store, "close", wraps=self.store.close) as close:
                with self.assertRaises(OSError):
                    make_server(port=reservation.getsockname()[1], static_dir=self.dist, config=self.config, store=self.store)
                close.assert_called_once()


class AuthBoundsTests(unittest.TestCase):
    def test_session_and_login_memory_are_bounded(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.auth"), "NAS authentication is missing")
        from backend.auth import SessionAuth
        auth = SessionAuth(PASSWORD, SECRET, clock=Clock(), max_sessions=2, max_clients=2)
        tokens = [auth.login(PASSWORD, "client") for _ in range(3)]
        self.assertFalse(auth.authenticated(tokens[0]))
        self.assertTrue(auth.authenticated(tokens[-1]))
        for index in range(6):
            with self.assertRaises(StoreError):
                auth.login("wrong", "client-" + str(index))
        self.assertLessEqual(len(auth._failures), 2)

    def test_concurrent_failed_login_budget_and_global_limit(self):
        self.assertIsNotNone(importlib.util.find_spec("backend.auth"), "NAS authentication is missing")
        from backend.auth import SessionAuth
        auth = SessionAuth(PASSWORD, SECRET, clock=Clock())
        def attempt(_index):
            try:
                auth.login("wrong", "same-peer")
            except StoreError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(results.count(401), 5)
        self.assertEqual(results.count(429), 7)
        for index in range(25):
            with self.assertRaises(StoreError) as caught:
                auth.login("wrong", "peer-" + str(index))
            self.assertEqual(caught.exception.status, 401)
        with self.assertRaises(StoreError) as caught:
            auth.login(PASSWORD, "another-peer")
        self.assertEqual(caught.exception.status, 429)


class LocalSessionTests(unittest.TestCase):
    def test_local_session_is_authenticated_without_cookie(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            server = make_server(port=0, db_path=Path(folder) / "local.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/api/session")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"authenticated": True, "auth_required": False, "storage": "sqlite"})
                self.assertIsNone(response.getheader("Set-Cookie"))
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_alternate_ipv4_loopback_accepts_its_own_exact_host(self):
        from backend.config import ServerConfig
        with tempfile.TemporaryDirectory() as folder:
            server = make_server(port=0, db_path=Path(folder) / "local.sqlite3", config=ServerConfig(host="127.0.0.2"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.2", server.server_port, timeout=2)
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"app": "filebackup-ledger", "status": "ok"})
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)


class NasConnectionBudgetTests(unittest.TestCase):
    def setUp(self):
        from backend.config import ServerConfig
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = create_store(Path(self.temp.name) / "ledger.sqlite3")
        import backend.server as server_module
        self.limits = server_module.HttpLimits(max_handlers=2, header_seconds=0.4, body_seconds=0.4, drain_seconds=0.2)
        self.server = make_server(port=0, config=ServerConfig.from_env(nas_environment()), store=self.store, limits=self.limits)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    request = NasServerTests.request
    login = NasServerTests.login

    def trickle(self, connection, prefix, *, duration=1.0):
        connection.sendall(prefix)
        stopped = threading.Event()
        def writer():
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline and not stopped.wait(0.07):
                try:
                    connection.sendall(b" ")
                except OSError:
                    break
        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        return stopped, thread

    def test_incomplete_header_trickle_expires_and_service_recovers(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            started = time.monotonic()
            stopped, writer = self.trickle(connection, b"GET /api/health HTTP/1.1\r\nHost: backup.example.test\r\nX-Slow:")
            try:
                try:
                    result = connection.recv(1024)
                except ConnectionResetError:
                    result = b""
                except TimeoutError:
                    self.fail("trickled headers retained a worker beyond the absolute budget")
                self.assertEqual(result, b"")
                self.assertLess(time.monotonic() - started, 0.85)
            finally:
                stopped.set()
                writer.join(2)
        self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_rejected_body_drain_has_absolute_deadline_during_trickle(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            prefix = b"POST /api/login HTTP/1.1\r\nHost: backup.example.test\r\nOrigin: https://evil.test\r\nContent-Type: application/json\r\nContent-Length: 8192\r\n\r\n "
            started = time.monotonic()
            stopped, writer = self.trickle(connection, prefix)
            try:
                response = http.client.HTTPResponse(connection)
                response.begin()
                self.assertEqual(response.status, 403)
                self.assertIn("error", json.loads(response.read()))
                self.assertLess(time.monotonic() - started, 0.65)
            finally:
                stopped.set()
                writer.join(2)
        self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_authenticated_body_trickle_expires_without_mutation(self):
        cookie = self.login()
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            prefix = ("POST /api/assets HTTP/1.1\r\nHost: backup.example.test\r\nOrigin: " + ORIGIN + "\r\nCookie: " + cookie + "\r\nContent-Type: application/json\r\nContent-Length: 8192\r\n\r\n ").encode()
            started = time.monotonic()
            stopped, writer = self.trickle(connection, prefix)
            try:
                try:
                    result = connection.recv(1024)
                except ConnectionResetError:
                    result = b""
                except TimeoutError:
                    self.fail("trickled body retained a worker beyond the absolute budget")
                self.assertEqual(result, b"")
                self.assertLess(time.monotonic() - started, 0.85)
            finally:
                stopped.set()
                writer.join(2)
        self.assertEqual(self.store.state()["assets"], [])
        self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_admission_saturation_rejects_extra_client_and_recovers(self):
        sockets = [socket.create_connection(("127.0.0.1", self.port), timeout=2) for _ in range(2)]
        try:
            for connection in sockets:
                connection.sendall(b"GET /api/health HTTP/1.1\r\n")
            # A third accepted connection must be rejected before a third
            # handler starts, even while it has not sent a request line yet.
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as extra:
                response = http.client.HTTPResponse(extra)
                try:
                    response.begin()
                except TimeoutError:
                    self.fail("an extra connection was admitted instead of rejected before spawning a worker")
                self.assertEqual(response.status, 503)
                self.assertIn("error", json.loads(response.read()))
        finally:
            for connection in sockets:
                connection.close()
        deadline = time.monotonic() + 2
        status = self.request("GET", "/api/health")[0]
        while status == 503:
            if time.monotonic() >= deadline:
                self.fail("admission slots did not recover after clients closed")
            time.sleep(0.01)
            status = self.request("GET", "/api/health")[0]
        self.assertEqual(status, 200)

    def test_slow_store_operation_after_full_body_is_not_cut_off_by_read_budget(self):
        cookie = self.login()
        original = self.store.create
        def slow_create(*args):
            time.sleep(self.limits.body_seconds + 0.15)
            return original(*args)
        with patch.object(self.store, "create", side_effect=slow_create):
            status, item, _ = self.request("POST", "/api/assets", {"name": "committed after read budget", "kind": "file"}, headers={"Cookie": cookie})
        self.assertEqual(status, 201)
        self.assertEqual(self.store.state()["assets"], [item])


class MongoHttpTests(unittest.TestCase):
    request = NasServerTests.request

    def test_readwrite_only_mongo_server_persists_across_http_restart(self):
        private_path = os.environ.get("DATABACK_TEST_MONGO_AUTH_URI_FILE")
        if not private_path:
            self.skipTest("authenticated isolated Mongo replica set is not configured")
        from pymongo import MongoClient
        from backend.config import ServerConfig
        root_uri = json.loads(Path(private_path).read_text(encoding="utf-8"))["root_uri"]
        database = "databack_test_" + uuid.uuid4().hex
        username, password = "http_test_" + secrets.token_hex(8), secrets.token_urlsafe(48)
        root_client = MongoClient(root_uri, serverSelectionTimeoutMS=5000)
        server = None
        thread = None
        try:
            root_client[database].command("createUser", username, pwd=password, roles=[{"role": "readWrite", "db": database}])
            parts = urlsplit(root_uri)
            query = [(key, value) for key, value in parse_qsl(parts.query) if key != "authSource"]
            query.append(("authSource", database))
            authority = quote(username, safe="") + ":" + quote(password, safe="") + "@" + parts.netloc.rsplit("@", 1)[-1]
            app_uri = urlunsplit((parts.scheme, authority, parts.path, urlencode(query), ""))
            config = ServerConfig.from_env(nas_environment(MONGODB_URI=app_uri, MONGODB_DATABASE=database))
            with tempfile.TemporaryDirectory() as folder:
                db_path = Path(folder) / "sqlite-must-not-exist.sqlite3"
                for run in range(2):
                    server = make_server(port=0, db_path=db_path, config=config)
                    self.port = server.server_port
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    self.assertEqual(self.request("GET", "/api/health")[:2], (200, {"app": "filebackup-ledger", "product": "databack", "status": "ok", "storage": "mongodb"}))
                    self.assertEqual(self.request("GET", "/api/export")[0], 401)
                    status, session, headers = self.request("POST", "/api/login", {"password": PASSWORD})
                    self.assertEqual(status, 200)
                    self.assertEqual(session["storage"], "mongodb")
                    signed = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
                    if run == 0:
                        status, item, _ = self.request("POST", "/api/assets", {"name": "HTTP manual", "kind": "file", "source_path": "//offline.invalid/manual"}, headers=signed)
                        self.assertEqual(status, 201)
                    self.assertEqual(self.request("GET", "/api/export", headers=signed)[1]["assets"], [item])
                    self.assertEqual(self.request("POST", "/api/logout", {}, headers=signed)[0], 200)
                    self.assertEqual(self.request("GET", "/api/state", headers=signed)[0], 401)
                    self.assertFalse(db_path.exists())
                    server.shutdown()
                    server.server_close()
                    thread.join(2)
                    server = None
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(2)
            root_client[database].command("dropUser", username)
            root_client.drop_database(database)
            root_client.close()


if __name__ == "__main__":
    unittest.main()
