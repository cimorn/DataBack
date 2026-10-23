"""Bounded password authentication and signed, revocable server sessions."""

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import OrderedDict, deque

from .store import StoreError
from .security import RateLimitError


COOKIE_NAME = "databack_session"
SESSION_SECONDS = 12 * 60 * 60
MAX_PASSWORD_CHARS = 1024
LOGIN_BODY_BYTES = 8192


def valid_password(value):
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PASSWORD_CHARS:
        return False
    try:
        return len(value.encode("utf-8")) <= 4096
    except UnicodeError:
        return False


class SessionAuth:
    def __init__(self, password, secret, *, clock=time.time, max_sessions=256, max_clients=256):
        self._clock = clock
        self._salt = secrets.token_bytes(32)
        self._password_digest = self._digest(password)
        self._secret = secret.encode("utf-8")
        self._sessions = OrderedDict()
        self._failures = OrderedDict()
        self._global_failures = deque(maxlen=30)
        self._max_sessions = max_sessions
        self._max_clients = max_clients
        self._lock = threading.RLock()

    def _digest(self, password):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self._salt, 200000)

    def _prune(self, now):
        for session, expiry in list(self._sessions.items()):
            if expiry <= now:
                del self._sessions[session]
        for client, failures in list(self._failures.items()):
            while failures and failures[0] <= now - 300:
                failures.popleft()
            if not failures:
                del self._failures[client]
        while self._global_failures and self._global_failures[0] <= now - 300:
            self._global_failures.popleft()

    def _signature(self, payload):
        return hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def login(self, password, client):
        if not valid_password(password):
            raise StoreError("密码格式无效。")
        # Serialize verification and attempt accounting so simultaneous failures
        # cannot bypass the budget. Only the socket peer identifies a client.
        with self._lock:
            now = self._clock()
            self._prune(now)
            retry = []
            failures = self._failures.get(client, ())
            if len(failures) >= 5:
                retry.append(failures[0] + 300 - now)
            if len(self._global_failures) >= 30:
                retry.append(self._global_failures[0] + 300 - now)
            if retry:
                raise RateLimitError(max(retry), "登录尝试过于频繁")
            if not hmac.compare_digest(self._digest(password), self._password_digest):
                failures = self._failures.setdefault(client, deque(maxlen=5))
                failures.append(now)
                self._failures.move_to_end(client)
                self._global_failures.append(now)
                while len(self._failures) > self._max_clients:
                    self._failures.popitem(last=False)
                raise StoreError("密码错误。", 401)
            self._failures.pop(client, None)
            session = secrets.token_urlsafe(32)
            expiry = int(now) + SESSION_SECONDS
            self._sessions[session] = expiry
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            payload = f"{session}.{expiry}"
            return payload + "." + self._signature(payload)

    def _session(self, token):
        if not isinstance(token, str) or len(token) > 160:
            return None
        parts = token.split(".")
        if (len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[0])
                or not re.fullmatch(r"[0-9]{1,12}", parts[1]) or not re.fullmatch(r"[a-f0-9]{64}", parts[2])):
            return None
        payload = ".".join(parts[:2])
        if not hmac.compare_digest(self._signature(payload), parts[2]):
            return None
        if self._sessions.get(parts[0]) != int(parts[1]):
            return None
        return parts[0]

    def authenticated(self, token):
        with self._lock:
            self._prune(self._clock())
            return self._session(token) is not None

    def revoke(self, token):
        with self._lock:
            self._prune(self._clock())
            session = self._session(token)
            if session is not None:
                self._sessions.pop(session, None)

    @staticmethod
    def cookie(token, *, secure=True, clear=False):
        flags = f"{COOKIE_NAME}={'' if clear else token}; Path=/; Max-Age={0 if clear else SESSION_SECONDS}; HttpOnly; SameSite=Strict"
        return flags + ("; Secure" if secure else "")
