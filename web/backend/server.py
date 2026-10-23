"""Same-origin ledger HTTP API with explicit authenticated NAS configuration."""

import argparse
import io
import json
import mimetypes
import re
import socket
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .auth import COOKIE_NAME, LOGIN_BODY_BYTES, SessionAuth, valid_password
from .config import ConfigError, ServerConfig
from .privacy import PRIVACY_COOKIE_NAME, PrivacyAuth, PrivacyStore
from .storage import create_store
from .snapshots import SnapshotManager
from .store import COLLECTIONS, StoreError, validate_entry, validate_id
from .security import (MAX_BATCH_BYTES, MAX_BATCH_RECORDS, MAX_NOTES_CHARS, MAX_WRITE_BYTES,
                       RateLimitError, SubmissionGuard, validate_submission_notes)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 10 * 1024 * 1024
STORAGE_UNAVAILABLE = "数据库暂时无法访问，请稍后重试并检查存储配置。"
SOCKET_IDLE_SECONDS = 15


def relative_export_path(value):
    """Remove machine anchors from legacy metadata, without accessing files."""
    value = value.replace("\\", "/").strip()
    if re.match(r"^[A-Za-z]:", value):
        value = value[2:]
    elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
        # Legacy fields are free text, including malformed URL authorities.
        # Strip the authority as text so one bad bracket cannot block export.
        value = value.partition("://")[2].partition("/")[2]
        value = value.split("?", 1)[0].split("#", 1)[0]
    elif value.startswith("//"):
        # Keep the share and its relative children; omit the NAS/computer name.
        value = "/".join(part for part in value.split("/") if part)
        value = value.partition("/")[2]
    value = re.sub(r"^/?[A-Za-z]:", "", value)
    parts = []
    for part in value.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def portable_export(payload):
    """Project downloadable paths onto the manual tree, retaining all IDs."""
    result = {**payload, **{key: [dict(item) for item in payload[key]] for key in COLLECTIONS}}
    assets = {asset["id"]: asset for asset in result["assets"]}
    for asset in result["assets"]:
        names, visited, cursor = [], set(), asset
        while cursor and cursor["id"] not in visited:
            visited.add(cursor["id"])
            names.append(cursor["name"])
            cursor = assets.get(cursor["parent_id"])
        path = relative_export_path("/".join(reversed(names)))
        # parent_id + name are the authoritative, lossless directory tree.
        # source_path is optional legacy metadata: don't synthesize a value
        # beyond its import limit or truncate a real stored path to fit it.
        asset["source_path"] = path if len(path) <= 8192 else relative_export_path(asset["source_path"])
    for collection, field in (("locations", "base_path"), ("records", "stored_path")):
        for item in result[collection]:
            item[field] = relative_export_path(item[field])
    return result


def resolve_location(store, payload):
    """Resolve typed names with the same canonical key as both storage engines."""
    if not isinstance(payload, dict) or set(payload) != {"name"}:
        raise StoreError("备份位置须只包含名称。")
    values = validate_entry("locations", {"name": payload["name"], "kind": "other"})
    key = values["name"].casefold()

    def existing():
        return next((item for item in store.state()["locations"] if item["name"].casefold() == key), None)

    found = existing()
    if found is not None:
        return found
    try:
        return store.create("locations", values)
    except StoreError as error:
        if error.status != 409:
            raise
        # Another request may have created the same key since the first read.
        found = existing()
        if found is None:
            raise
        return found


@dataclass(frozen=True)
class HttpLimits:
    """Conservative production budgets, injectable for isolated socket checks."""

    max_handlers: int = 32
    header_seconds: float = 15
    body_seconds: float = 30
    drain_seconds: float = 0.25


class DeadlineReader(io.RawIOBase):
    """Check an absolute deadline on every recv, including buffered read loops."""

    def __init__(self, connection, deadline):
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        timeout = SOCKET_IDLE_SECONDS
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request read deadline expired")
            timeout = min(timeout, remaining)
        self.connection.settimeout(timeout)
        return self.connection.recv_into(buffer)


def reject_constant(_value):
    raise ValueError("JSON constants must be standard")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class LedgerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, store, static_dir, config, auth, limits, *, submission_guard=None, snapshot_dir=None):
        self.store = store
        self.config = config
        self.auth = auth
        self.privacy = PrivacyAuth(config.privacy_password)
        self.privacy_lock = threading.RLock()
        self.limits = limits
        self.submissions = submission_guard if submission_guard is not None else SubmissionGuard()
        self._store_closed = False
        try:
            self._handler_slots = threading.BoundedSemaphore(limits.max_handlers)
            self.static_dir = Path(static_dir).resolve()
            snapshot_dir = config.snapshot_dir or snapshot_dir or (Path(store.db_path).parent / 'snapshots' if hasattr(store, 'db_path') else PROJECT_ROOT / 'data' / 'snapshots')
            self.snapshots = SnapshotManager(store, snapshot_dir, self.privacy_lock, static_dir=self.static_dir)
            if ":" in address[0]:
                self.address_family = socket.AF_INET6
            super().__init__(address, LedgerHandler)
        except Exception:
            # HTTPServer already invokes server_close on bind failure. Failures
            # before it creates a socket still release the owned store once.
            if hasattr(self, "socket"):
                self.server_close()
            elif not self._store_closed:
                self._store_closed = True
                self.store.close()
            raise

    def serve_forever(self, poll_interval=0.5):
        self.snapshots.start()
        return super().serve_forever(poll_interval=poll_interval)

    def process_request(self, request, client_address):
        # Admission happens in the accept loop before ThreadingMixIn creates a
        # worker. A full server emits a small best-effort 503 with bounded I/O.
        if not self._handler_slots.acquire(blocking=False):
            body = b'{"error":"Service temporarily busy; retry shortly."}'
            response = ("HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n"
                        "Content-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\n"
                        "X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n").encode("ascii") + body
            try:
                request.settimeout(0.05)
                request.sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._handler_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handler_slots.release()

    def server_close(self):
        try:
            super().server_close()
        finally:
            if not self._store_closed:
                self._store_closed = True
                if hasattr(self, 'snapshots'):
                    self.snapshots.close()
                self.store.close()

    def handle_error(self, _request, _client_address):
        # Never allow socketserver's default traceback to expose credentials
        # carried inside a database or request exception.
        pass


class LedgerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FileBackup"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(SOCKET_IDLE_SECONDS)
        self.rfile.close()
        self._reader = DeadlineReader(self.connection, time.monotonic() + self.server.limits.header_seconds)
        self.rfile = io.BufferedReader(self._reader)

    def _read_budget(self, seconds=None):
        self._reader.deadline = None if seconds is None else time.monotonic() + seconds
        self.connection.settimeout(SOCKET_IDLE_SECONDS)

    def log_message(self, _format, *_args):
        # Request paths, identifiers and bodies never enter server logs.
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", *, cookie=None, retry_after=None):
        self._read_budget()
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        if cookie is not None:
            for value in cookie if isinstance(cookie, (list, tuple)) else (cookie,):
                self.send_header("Set-Cookie", value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self._send(code, {"error": "HTTP 请求格式或请求方法无效。"})

    def _security(self):
        hosts = self.headers.get_all("Host", [])
        config = self.server.config
        permitted = config.allowed_hosts if config.auth_required else {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if not config.auth_required:
            permitted.add(f"{config.host}:{self.server.server_port}")
        if not config.auth_required and ":" in config.host:
            permitted = {f"[{config.host}]:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if len(hosts) != 1 or hosts[0] not in permitted:
            raise StoreError("请通过允许的台账地址访问。", 403)
        origins = self.headers.get_all("Origin", [])
        origin = config.public_origin if config.auth_required else "http://" + hosts[0]
        if origins and (len(origins) != 1 or origins[0] != origin):
            raise StoreError("请求来源与台账地址不一致。", 403)
        # BaseHTTPRequestHandler reduces leading // in self.path. The retained
        # request line contains the original target, so validate it before URL
        # parsing can also discard a fragment and route a different request.
        target = self.requestline.split()[1]
        if not target.startswith("/") or target.startswith("//") or "#" in target:
            raise StoreError("请求地址无效。")
        try:
            path = unquote(urlsplit(target).path, errors="strict")
        except (UnicodeError, ValueError):
            raise StoreError("请求地址编码无效。") from None
        if "\x00" in path:
            raise StoreError("请求地址无效。")
        mutation = self.command not in ("GET", "HEAD", "OPTIONS")
        auth_mutation = path in ("/api/login", "/api/logout") and self.command == "POST"
        if (config.auth_required and mutation or auth_mutation) and len(origins) != 1:
            raise StoreError("此请求须提供与台账地址一致的来源。", 403)
        return path

    def _token(self, name=COOKIE_NAME):
        cookies = self.headers.get_all("Cookie", [])
        if len(cookies) != 1 or len(cookies[0]) > 8192:
            return None
        values = []
        for part in cookies[0].split(";"):
            key, separator, value = part.strip().partition("=")
            if key == name and separator:
                values.append(value)
        return values[0] if len(values) == 1 else None

    def _session_state(self):
        required = self.server.config.auth_required
        return {"authenticated": not required or self.server.auth.authenticated(self._token()),
                "auth_required": required, "storage": self.server.store.backend}

    def _body(self, allow_empty=False, *, maximum=MAX_WRITE_BYTES):
        if getattr(self, '_payload_cached', False):
            return self._cached_payload
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
            raise StoreError("保存请求必须使用 application/json 格式。", 415)
        if self.headers.get("Transfer-Encoding") is not None:
            raise StoreError("不支持分块请求，请使用完整 JSON 请求。")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths and allow_empty:
            return None
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise StoreError("请求正文长度无效。")
        if len(lengths[0]) > 10:
            raise StoreError("请求正文长度无效。")
        size = int(lengths[0])
        if size > maximum:
            raise StoreError("请求内容超过允许的大小限制。", 413)
        if not size and allow_empty:
            return None
        self._read_budget(self.server.limits.body_seconds)
        try:
            raw = self.rfile.read(size)
            self._body_read = True
            if len(raw) != size:
                raise ValueError("truncated body")
            return json.loads(raw.decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (UnicodeError, ValueError, RecursionError):
            raise StoreError("正文不是有效的 UTF-8 JSON，请检查文件内容。") from None
        finally:
            # Only transport reads have this deadline. Mongo operations after
            # a complete body retain their independent driver timeouts, without
            # a background timer closing the response socket during a write.
            self._read_budget()

    def _discard_body(self):
        # On Windows, closing with an unread POST body can reset the socket
        # before the client receives a validation response. Drain only a bounded,
        # correctly framed body; an unresponsive client cannot occupy this thread.
        if getattr(self, "_body_read", False):
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            if self.command in ("POST", "PUT", "DELETE", "PATCH"):
                # Rejected unframed bodies can otherwise reset a Windows socket
                # before its validation response is received. Discard one small
                # available chunk only, with the existing absolute drain deadline;
                # this does not interpret or accept unsupported HTTP framing.
                self._read_budget(self.server.limits.drain_seconds)
                try:
                    self.rfile.read1(8192)
                except OSError:
                    pass
                finally:
                    self._read_budget()
            return
        if len(lengths[0]) > 10:
            return
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY_BYTES:
            return
        self._read_budget(self.server.limits.drain_seconds)
        try:
            self.rfile.read(size)
        except OSError:
            pass
        finally:
            self._read_budget()

    def _static(self, path):
        if "\\" in path or ".." in path.split("/"):
            raise StoreError("不能访问应用目录以外的文件。", 403)
        target = (self.server.static_dir / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if not target.is_relative_to(self.server.static_dir):
            raise StoreError("不能访问应用目录以外的文件。", 403)
        if target.suffix.lower() in (".py", ".pyc", ".sqlite3", ".sqlite", ".db", ".env") or any(part.startswith(".") for part in Path(path).parts if part not in ("/", "\\")):
            raise StoreError("不能访问应用数据或源文件。", 403)
        if not target.is_file():
            raise StoreError("页面文件不存在，请确认前端已完成构建。", 404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or target.suffix == ".js":
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def _notes(self, payload, collection=None, item_id=None):
        previous = None
        if (collection and item_id and isinstance(payload, dict)
                and isinstance(payload.get("notes"), str) and len(payload["notes"]) > MAX_NOTES_CHARS):
            existing = next((item for item in self._ledger().state()[collection] if item["id"] == item_id), None)
            previous = existing["notes"] if existing else None
        validate_submission_notes(payload, previous_notes=previous)

    def _route(self):
        self._read_budget()
        path = self._security()
        method = "GET" if self.command == "HEAD" else self.command
        if path in ("/api/login", "/api/logout"):
            if self.command != "POST":
                raise StoreError("此接口仅支持 POST 请求。", 405)
            if path == "/api/login":
                self.server.submissions.consume("login", self.client_address[0])
            if urlsplit(self.path).query:
                raise StoreError("认证请求不接受查询参数。")
            payload = self._body(allow_empty=path == "/api/logout", maximum=LOGIN_BODY_BYTES)
            cookie = None
            if path == "/api/login":
                if not isinstance(payload, dict) or set(payload) != {"password"}:
                    raise StoreError("登录内容必须只包含 password 字段。")
                password = payload["password"]
                if not valid_password(password):
                    raise StoreError("密码格式无效。")
                if self.server.config.auth_required:
                    token = self.server.auth.login(password, self.client_address[0])
                    self.server.auth.revoke(self._token())
                    cookie = self.server.auth.cookie(token, secure=self.server.config.secure_cookies)
                    state = {"authenticated": True, "auth_required": True, "storage": self.server.store.backend}
                else:
                    state = self._session_state()
            else:
                if payload not in (None, {}):
                    raise StoreError("退出登录无需额外字段。")
                if self.server.config.auth_required:
                    self.server.auth.revoke(self._token())
                    cookie = self.server.auth.cookie(None, clear=True, secure=self.server.config.secure_cookies)
                with self.server.privacy_lock:
                    self.server.privacy.revoke_main(self._token())
                privacy_cookie = self.server.privacy.cookie(clear=True, secure=self.server.config.secure_cookies)
                cookie = [cookie, privacy_cookie] if cookie is not None else privacy_cookie
                state = self._session_state()
                if self.server.config.auth_required:
                    state["authenticated"] = False
            self._send(200, state, cookie=cookie)
            return
        if (self.server.config.auth_required and (path == "/api" or path.startswith("/api/"))
                and path not in ("/api/health", "/api/session") and not self.server.auth.authenticated(self._token())):
            raise StoreError("请先登录台账。", 401)
        if method == 'POST' and path in ('/api/privacy/unlock', '/api/privacy/lock'):
            # Fail closed even if submission admission or body validation fails.
            with self.server.privacy_lock:
                self.server.privacy.revoke_main(self._token())
        if method in ("POST", "PUT", "DELETE") and path.startswith("/api/"):
            self.server.submissions.consume("write", self.client_address[0])
            if path == "/api/import":
                self.server.submissions.consume("import", self.client_address[0])
            if path == '/api/assets/bulk':
                self.server.submissions.consume('directory_bulk', self.client_address[0])
            if path == '/api/snapshots' or path.startswith('/api/snapshots/') and path.endswith('/restore'):
                self.server.submissions.consume('import', self.client_address[0])
        parts = path.split('/')
        privacy_password = (path == '/api/privacy/unlock' or
                            len(parts) == 5 and parts[1:3] == ['api', 'assets'] and parts[4] == 'privacy')
        if method == 'POST' and privacy_password:
            self.server.submissions.consume('login', self.client_address[0])
        recovery_action = method == 'POST' and (path == '/api/snapshots' or len(parts) == 5 and parts[2] in ('trash', 'snapshots') and parts[4] == 'restore')
        has_body = (method in ('PUT', 'DELETE') and len(parts) == 4 and parts[2] in COLLECTIONS
                    or recovery_action
                    or method == 'POST' and (path in ('/api/assets/bulk', '/api/assets/reorder', '/api/locations/resolve', '/api/records/batch', '/api/import', '/api/privacy/unlock', '/api/privacy/lock')
                                             or len(parts) == 3 and parts[2] in COLLECTIONS
                                             or privacy_password))
        if has_body:
            maximum = MAX_BODY_BYTES if path == '/api/import' else MAX_BATCH_BYTES if path in ('/api/records/batch', '/api/assets/bulk') else LOGIN_BODY_BYTES if privacy_password else MAX_WRITE_BYTES
            self._cached_payload = self._body(allow_empty=method == 'DELETE' or recovery_action, maximum=maximum)
            self._payload_cached = True
        # Never reserve the privacy lock while reading an untrusted request
        # body or checking its origin. The snapshot and mutation are serialized.
        with self.server.privacy_lock:
            if (self.server.config.auth_required and path.startswith('/api/')
                    and path not in ('/api/health', '/api/session')
                    and not self.server.auth.authenticated(self._token())):
                raise StoreError('请先登录台账。', 401)
            self._dispatch(path, method)

    def _unlocked(self):
        proofs = self.headers.get_all('X-Databack-Privacy', [])
        proof = proofs[0] if len(proofs) == 1 else None
        return self.server.privacy.authenticated(self._token(PRIVACY_COOKIE_NAME), self._token(), proof)

    def _ledger(self):
        if not hasattr(self, '_privacy_store'):
            self._privacy_store = PrivacyStore(self.server.store, self._unlocked())
        return self._privacy_store

    def _dispatch(self, path, method):
        parts = path.split('/')
        if path == '/api/snapshots' or path.startswith('/api/snapshots/'):
            if not self._unlocked():
                raise StoreError('请先验证私密资料访问密码。', 403)
            if method == 'GET' and path == '/api/snapshots':
                self._send(200, self.server.snapshots.list())
                return
            if method == 'GET' and len(parts) == 4:
                self._send(200, portable_export(self.server.snapshots.download(parts[3])))
                return
            if method == 'POST' and (path == '/api/snapshots' or len(parts) == 5 and parts[4] == 'restore'):
                if self._body(allow_empty=True) not in (None, {}):
                    raise StoreError('备份操作无需额外字段。')
                if path == '/api/snapshots':
                    self._send(201, self.server.snapshots.create())
                else:
                    self._send(200, self.server.snapshots.restore(parts[3]))
                return
        if path == '/api/trash' and method == 'GET':
            self._send(200, {'items': self._ledger().trash()})
            return
        if method == 'POST' and len(parts) == 5 and parts[1:3] == ['api', 'trash'] and parts[4] == 'restore':
            if self._body(allow_empty=True) not in (None, {}):
                raise StoreError('恢复操作无需额外字段。')
            self._send(200, self._ledger().restore_trash(parts[3]))
            return
        if path == '/api/privacy' and method == 'GET':
            self._send(200, {'unlocked': self._unlocked()})
            return
        if path in ('/api/privacy/unlock', '/api/privacy/lock') and method == 'POST':
            payload = self._body()
            unlocking = path.endswith('/unlock')
            if not isinstance(payload, dict) or set(payload) != ({'password'} if unlocking else set()):
                raise StoreError('密码请求内容无效。')
            # A failed or throttled unlock must never retain an earlier grant.
            self.server.privacy.revoke_main(self._token())
            accepted = unlocking and self.server.privacy.verify(payload['password'], self.client_address[0])
            token, proof = self.server.privacy.grant(self._token()) if accepted else (None, None)
            result = {'unlocked': bool(accepted)}
            if accepted:
                result['token'] = proof
            self._send(200, result, cookie=self.server.privacy.cookie(token, secure=self.server.config.secure_cookies, clear=not accepted))
            return
        if (method == 'POST' and len(parts) == 5 and parts[1:3] == ['api', 'assets'] and parts[4] == 'privacy'):
            payload = self._body()
            if not isinstance(payload, dict) or set(payload) != {'password', 'private'} or type(payload['private']) is not bool:
                raise StoreError('隐私设置内容无效。')
            if not self.server.privacy.verify(payload['password'], self.client_address[0]):
                self._send(200, {'changed': False})
                return
            self._ledger()._check('assets', parts[3])
            self.server.store.set_private(validate_id(parts[3]), payload['private'])
            self._send(200, {'changed': True})
            return
        if method == "GET":
            if path == "/api/health":
                try:
                    if not self.server.store.readiness():
                        raise StoreError(STORAGE_UNAVAILABLE, 503)
                except Exception:
                    raise StoreError(STORAGE_UNAVAILABLE, 503) from None
                health = {"app": "filebackup-ledger", "status": "ok"}
                if self.server.config.auth_required:
                    health.update({"product": "databack", "storage": self.server.store.backend})
                self._send(200, health)
            elif path == "/api/session":
                self._send(200, self._session_state())
            elif path == "/api/state":
                self._send(200, self._ledger().state())
            elif path == "/api/export":
                self._send(200, portable_export(self._ledger().export()))
            elif path == "/api" or path.startswith("/api/"):
                raise StoreError("未找到该接口。", 404)
            else:
                self._static(path)
            return
        parts = path.split("/")
        if method == 'POST' and path == '/api/assets/bulk':
            payload = self._body(maximum=MAX_BATCH_BYTES)
            if not isinstance(payload, dict) or set(payload) != {'parent_id', 'kind', 'paths'}:
                raise StoreError('批量新建须包含所属目录、类型和路径。')
            self._send(201, self._ledger().bulk_assets(payload['parent_id'], payload['kind'], payload['paths']))
        elif method == 'PUT' and path == '/api/records/bulk':
            from .recovery import validate_bulk_records
            payload = self._body()
            if not isinstance(payload, dict) or set(payload) != {'ids', 'changes'}:
                raise StoreError('批量修改须包含记录和修改字段。')
            ids = validate_bulk_records(payload['ids'], payload['changes'])
            self.server.submissions.consume('record', self.client_address[0], cost=len(ids))
            self._send(200, self._ledger().bulk_records(ids, payload['changes']))
        elif method == "POST" and path == "/api/assets/reorder":
            payload = self._body()
            if not isinstance(payload, dict) or set(payload) != {"parent_id", "ordered_ids"}:
                raise StoreError("排序内容须包含 parent_id 和 ordered_ids。")
            self._send(200, self._ledger().reorder_assets(payload["parent_id"], payload["ordered_ids"]))
        elif method == "POST" and path == "/api/locations/resolve":
            payload = self._body()
            if isinstance(payload, dict):
                self._ledger().resolve_location(payload.get('name'))
            self._send(200, resolve_location(self._ledger(), payload))
        elif method == "POST" and path == "/api/records/batch":
            payload = self._body(maximum=MAX_BATCH_BYTES)
            if not isinstance(payload, dict) or set(payload) not in ({"records"}, {"records", "mark_asset_id"}):
                raise StoreError("批量登记须包含 records 数组，可选 mark_asset_id。")
            records = payload["records"]
            if not isinstance(records, list) or not records:
                raise StoreError("请至少登记一条备份记录。")
            if len(records) > MAX_BATCH_RECORDS:
                raise StoreError(f"单次最多登记 {MAX_BATCH_RECORDS} 条备份记录。", 413)
            for record in records:
                self._notes(record)
            self.server.submissions.consume("record", self.client_address[0], cost=len(records))
            options = {"mark_asset_id": validate_id(payload["mark_asset_id"])} if "mark_asset_id" in payload else {}
            self._send(201, {"records": self._ledger().create_batch(records, **options)})
        elif method == "POST" and path == "/api/import":
            self._send(200, self._ledger().import_data(self._body(maximum=MAX_BODY_BYTES)))
        elif method == "POST" and len(parts) == 3 and parts[1] == "api" and parts[2] in COLLECTIONS:
            payload = self._body()
            self._notes(payload)
            if parts[2] == "records":
                self.server.submissions.consume("record", self.client_address[0])
            self._send(201, self._ledger().create(parts[2], payload))
        elif method in ("PUT", "DELETE") and len(parts) == 4 and parts[1] == "api" and parts[2] in COLLECTIONS and parts[3]:
            if method == "PUT":
                payload = self._body()
                self._notes(payload, parts[2], parts[3])
                if parts[2] == "records":
                    self.server.submissions.consume("record", self.client_address[0])
                result = self._ledger().update(parts[2], parts[3], payload)
            else:
                payload = self._body(allow_empty=True)
                if payload not in (None, {}):
                    raise StoreError("删除请求无需额外字段。")
                result = self._ledger().delete(parts[2], parts[3])
            self._send(200, result)
        elif path == "/api" or path.startswith("/api/"):
            raise StoreError("未找到该接口或请求方法。", 404)
        else:
            raise StoreError("请求方法不受支持。", 405)

    def _handle(self):
        try:
            self._route()
        except StoreError as error:
            self._discard_body()
            retry = error.retry_after if isinstance(error, RateLimitError) else None
            body = {"error": STORAGE_UNAVAILABLE if error.status == 503 else str(error)}
            if retry is not None:
                body["retry_after"] = retry
            self._send(error.status, body, retry_after=retry)
        except (TimeoutError, ConnectionError):
            self.close_connection = True
        except sqlite3.OperationalError:
            self._send(503, {"error": STORAGE_UNAVAILABLE})
        except Exception:
            self._discard_body()
            self._send(500, {"error": "服务暂时无法完成请求，请检查运行环境。"})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_OPTIONS = _handle
    do_PATCH = _handle


def make_server(port=8767, db_path=None, static_dir=None, *, config=None, store=None, auth=None, limits=None, submission_guard=None):
    """Create a server without starting it, for launchers and isolated checks."""
    if config is None:
        config = ServerConfig.from_env(port=None if port == 8767 else port)
    selected_port = config.port if port == 8767 else port
    selected_store = store
    try:
        if selected_store is None:
            selected_store = create_store(db_path, mongo_uri=config.mongo_uri, mongo_database=config.mongo_database)
        if config.auth_required and auth is None:
            auth = SessionAuth(config.admin_password, config.session_secret)
    except Exception:
        if selected_store is not None:
            selected_store.close()
        raise
    # The HTTP server owns the store from here, including construction failures.
    return LedgerServer((config.host, selected_port), selected_store,
                        static_dir if static_dir is not None else PROJECT_ROOT / "dist", config, auth,
                        limits if limits is not None else HttpLimits(), submission_guard=submission_guard,
                        snapshot_dir=Path(db_path).resolve().parent / 'snapshots' if db_path is not None else None)


def main(argv=None):
    # Hidden Windows launchers redirect logs, where the inherited locale may be
    # cp1252. Keep Chinese startup/error messages safe regardless of that locale.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="本地文件备份记录台账")
    parser.add_argument("--port", type=int, default=None, help="服务端口（默认 8767，可使用 DATABACK_PORT）")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 数据库路径")
    args = parser.parse_args(argv)
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间。")
    try:
        config = ServerConfig.from_env(port=args.port)
        server = make_server(db_path=args.db, config=config)
    except ConfigError as error:
        print("启动失败：" + str(error), file=sys.stderr)
        return 1
    except Exception:
        port = args.port if args.port is not None else 8767
        print(f"启动失败：无法监听服务端口 {port} 或打开数据库。请检查部署配置、端口占用和目录权限。", file=sys.stderr)
        return 1
    address = config.public_origin if config.auth_required else f"http://127.0.0.1:{server.server_port}"
    print(f"备份台账已启动：{address}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
