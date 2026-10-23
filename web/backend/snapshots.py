"""Durable daily ledger snapshots, with bounded reads and atomic replacement."""

import gzip
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .store import StoreError, validate_id, normalize_time
from .recovery import full_state

OWNED_NAME = re.compile(r'^databack-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json\.gz$')
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
UNAVAILABLE = '自动备份暂不可用，请检查备份目录。'


class SnapshotManager:
    def __init__(self, store, directory, lock, *, now=None, static_dir=None):
        self.store = store
        self.directory = Path(directory).resolve()
        self.lock = lock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.error = None
        self.stop = threading.Event()
        self.thread = None
        self.unsafe = static_dir is not None and (self.directory == Path(static_dir).resolve() or Path(static_dir).resolve() in self.directory.parents)

    def _root(self):
        if self.unsafe:
            raise StoreError(UNAVAILABLE, 503)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.directory

    def _path(self, snapshot_id):
        validate_id(snapshot_id)
        root = self._root()
        path = root / ('databack-' + snapshot_id + '.json.gz')
        if path.is_symlink() or path.resolve().parent != root:
            raise StoreError('备份文件不可访问。', 404)
        return path

    def _read(self, snapshot_id):
        try:
            path = self._path(snapshot_id)
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise ValueError('snapshot too large')
            with gzip.open(path, 'rb') as stream:
                raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                raise ValueError('expanded snapshot too large')
            snapshot = json.loads(raw)
            if (not isinstance(snapshot, dict) or set(snapshot) != {'id', 'created_at', 'reason', 'payload'}
                    or snapshot['id'] != snapshot_id or snapshot['reason'] not in ('manual', 'daily', 'before_restore')):
                raise ValueError('invalid snapshot envelope')
            normalize_time(snapshot['created_at'], 'created_at')
            full_state(snapshot['payload'])
            return snapshot
        except FileNotFoundError:
            raise StoreError('备份不存在。', 404) from None
        except StoreError:
            raise
        except (OSError, ValueError, TypeError, KeyError, EOFError, RecursionError):
            raise StoreError('备份文件损坏或无法读取。', 409) from None

    @staticmethod
    def _summary(snapshot):
        return {key: snapshot[key] for key in ('id', 'created_at', 'reason')} | {
            'counts': {key: len(snapshot['payload'][key]) for key in ('assets', 'records')}}

    def _items(self):
        items = []
        for path in self._root().iterdir():
            match = OWNED_NAME.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                continue
            try:
                items.append(self._summary(self._read(match.group(1))))
            except StoreError:
                continue
        items.sort(key=lambda item: (datetime.fromisoformat(item['created_at'].replace('Z', '+00:00')), item['id']), reverse=True)
        return items

    def list(self):
        with self.lock:
            try:
                items = self._items()
                return {'items': items, 'enabled': self.error is None,
                        **({'error': self.error} if self.error else {})}
            except (OSError, StoreError):
                self.error = UNAVAILABLE
                return {'items': [], 'enabled': False, 'error': self.error}

    def create(self, reason='manual'):
        if reason not in ('manual', 'daily', 'before_restore'):
            raise ValueError('unknown snapshot reason')
        with self.lock:
            temp = None
            try:
                snapshot = {'id': str(uuid.uuid4()), 'created_at': self.now().astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                            'reason': reason, 'payload': self.store.export()}
                full_state(snapshot['payload'])
                raw = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
                if len(raw) > MAX_SNAPSHOT_BYTES:
                    raise StoreError('台账过大，无法创建自动备份。', 413)
                target = self._path(snapshot['id'])
                temp = target.with_name('.pending-' + uuid.uuid4().hex)
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0) as packed:
                        packed.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, target)
                temp = None
                # Filesystem data is durable before retention may remove copies.
                if os.name != 'nt':
                    dir_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                # Always retain the just-written copy, even if the system clock
                # moved backwards before a restore or several timestamps tie.
                others = [item for item in self._items() if item['id'] != snapshot['id']]
                for old in others[29:]:
                    self._path(old['id']).unlink()
                self.error = None
                return self._summary(snapshot)
            except OSError:
                self.error = UNAVAILABLE
                raise StoreError(UNAVAILABLE, 503) from None
            finally:
                if temp is not None:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass

    def download(self, snapshot_id):
        with self.lock:
            return self._read(snapshot_id)['payload']

    def restore(self, snapshot_id):
        with self.lock:
            payload = self._read(snapshot_id)['payload']
            self.create('before_restore')
            return self.store.restore_state(payload)

    def ensure_daily(self):
        with self.lock:
            try:
                today = self.now().astimezone(timezone.utc).date().isoformat()
                if not any(item['reason'] == 'daily' and item['created_at'][:10] == today for item in self._items()):
                    self.store.trash_entries()
                    self.create('daily')
            except Exception:
                # The app can stay available; the authenticated UI reports this
                # state rather than silently pretending the daily backup worked.
                self.error = UNAVAILABLE

    def start(self):
        if self.thread is not None:
            return
        def run():
            while not self.stop.is_set():
                self.ensure_daily()
                self.stop.wait(60)
        self.thread = threading.Thread(target=run, name='databack-daily-snapshots', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=20)
