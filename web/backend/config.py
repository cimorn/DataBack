"""Explicit HTTP/storage settings; secret values never enter diagnostics."""

import ipaddress
import os
import re
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """A sanitized, actionable configuration failure."""


def _host(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 253:
        return False
    try:
        parsed = urlsplit("https://" + value)
        if parsed.netloc != value or parsed.path or parsed.query or parsed.fragment or parsed.username is not None:
            return False
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return False
        name = parsed.hostname
        if not name or name != name.lower() or value.endswith(":"):
            return False
        if ":" in name:
            ipaddress.IPv6Address(name)
            return value.startswith("[")
        return all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split("."))
    except (ValueError, UnicodeError):
        return False


def _loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _strong(value, minimum, unique):
    return (isinstance(value, str) and minimum <= len(value) <= 1024
            and len(value.encode("utf-8")) <= 4096 and len(set(value)) >= unique
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8767
    public_origin: str | None = None
    allowed_hosts: frozenset = field(default_factory=frozenset)
    admin_password: str | None = field(default=None, repr=False)
    session_secret: str | None = field(default=None, repr=False)
    mongo_uri: str | None = field(default=None, repr=False)
    mongo_database: str = "databack"
    allow_short_password: bool = False
    privacy_password: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    snapshot_dir: str | None = None

    def __post_init__(self):
        if self.snapshot_dir is not None and (not isinstance(self.snapshot_dir, str) or not self.snapshot_dir.strip() or '\x00' in self.snapshot_dir):
            raise ConfigError('DATABACK_SNAPSHOT_DIR 须为有效的备份目录。')
        if not _strong(self.privacy_password, 1, 1):
            raise ConfigError("DATABACK_PRIVACY_PASSWORD 须为非空、无控制字符且不超过 1024 字符和 4096 字节的密码。")
        if not isinstance(self.allow_short_password, bool):
            raise ConfigError("DATABACK_ALLOW_SHORT_PASSWORD 必须是 true 或 false。")
        if self.host != "localhost":
            try:
                ipaddress.ip_address(self.host)
            except (ValueError, TypeError):
                raise ConfigError("DATABACK_HOST 必须是明确的监听 IP 地址或 localhost。") from None
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            raise ConfigError("DATABACK_PORT 必须是有效端口。")
        if self.mongo_uri is not None and (not isinstance(self.mongo_uri, str) or not self.mongo_uri.strip()):
            raise ConfigError("MONGODB_URI 已设置但为空。")
        if not isinstance(self.mongo_database, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", self.mongo_database):
            raise ConfigError("MONGODB_DATABASE 名称无效。")
        if self.auth_required:
            if self.allow_short_password and not _strong(self.admin_password, 1, 1):
                raise ConfigError("DATABACK_ADMIN_PASSWORD 须为非空、无控制字符且不超过 1024 字符和 4096 字节的密码。")
            if not self.allow_short_password and not _strong(self.admin_password, 16, 8):
                raise ConfigError("网络部署须设置至少 16 字符且字符多样的 DATABACK_ADMIN_PASSWORD。")
            if not _strong(self.session_secret, 32, 12):
                raise ConfigError("网络部署须设置至少 32 字符且字符多样的 DATABACK_SESSION_SECRET。")
            try:
                origin = urlsplit(self.public_origin or "")
            except ValueError:
                raise ConfigError("DATABACK_PUBLIC_ORIGIN 必须是完整且无路径的 HTTPS 地址（本机可用 HTTP）。") from None
            if ((origin.scheme != "https" and not (origin.scheme == "http" and _loopback(origin.hostname))) or not _host(origin.netloc) or origin.path or origin.query
                    or origin.fragment or self.public_origin != origin.scheme + "://" + origin.netloc):
                raise ConfigError("DATABACK_PUBLIC_ORIGIN 必须是完整且无路径的 HTTPS 地址（本机可用 HTTP）。")
            if (not self.allowed_hosts or not isinstance(self.allowed_hosts, frozenset)
                    or not all(_host(host) for host in self.allowed_hosts)
                    or origin.netloc not in self.allowed_hosts):
                raise ConfigError("DATABACK_ALLOWED_HOSTS 须列出精确地址并包含公开来源地址，不支持通配符。")

    @property
    def secure_cookies(self):
        return bool(self.public_origin and self.public_origin.startswith("https://"))

    @property
    def auth_required(self):
        return (not _loopback(self.host) or self.public_origin is not None or bool(self.allowed_hosts)
                or self.admin_password is not None or self.session_secret is not None)

    @classmethod
    def from_env(cls, environ=None, *, port=None):
        values = os.environ if environ is None else environ
        try:
            resolved_port = int(values.get("DATABACK_PORT", "8767")) if port is None else port
        except (ValueError, TypeError):
            raise ConfigError("DATABACK_PORT 必须是有效端口。") from None
        if port is None and not 1 <= resolved_port <= 65535:
            raise ConfigError("DATABACK_PORT 必须在 1 到 65535 之间。")
        allow_short_password = values.get("DATABACK_ALLOW_SHORT_PASSWORD", "false")
        if not isinstance(allow_short_password, str) or allow_short_password not in ("true", "false"):
            raise ConfigError("DATABACK_ALLOW_SHORT_PASSWORD 必须是 true 或 false。")
        hosts = values.get("DATABACK_ALLOWED_HOSTS")
        if hosts is not None and (not hosts or any(not part.strip() for part in hosts.split(","))):
            raise ConfigError("DATABACK_ALLOWED_HOSTS 须列出精确地址，不允许空地址。")
        config = cls(
            host=values.get("DATABACK_HOST", "127.0.0.1"), port=resolved_port,
            public_origin=values.get("DATABACK_PUBLIC_ORIGIN"),
            allowed_hosts=frozenset(part.strip() for part in hosts.split(",")) if hosts else frozenset(),
            admin_password=values.get("DATABACK_ADMIN_PASSWORD"), session_secret=values.get("DATABACK_SESSION_SECRET"),
            mongo_uri=values.get("MONGODB_URI"), mongo_database=values.get("MONGODB_DATABASE", "databack"),
            allow_short_password=allow_short_password == "true",
            privacy_password=values.get("DATABACK_PRIVACY_PASSWORD", secrets.token_urlsafe(32)),
            snapshot_dir=values.get('DATABACK_SNAPSHOT_DIR'),
        )
        if config.auth_required and 'DATABACK_PRIVACY_PASSWORD' not in values:
            raise ConfigError('网络部署须设置独立的 DATABACK_PRIVACY_PASSWORD。')
        return config
