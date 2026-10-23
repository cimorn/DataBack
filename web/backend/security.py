"""Bounded, atomic submission budgets and validation at the HTTP boundary."""

import math
import threading
import time
from collections import OrderedDict, deque

from .validation import StoreError

MAX_NOTES_CHARS = 2000
MAX_BATCH_RECORDS = 20
MAX_WRITE_BYTES = 128 * 1024
MAX_BATCH_BYTES = 256 * 1024
PEER_RULES = {
    "login": ((10, 60),),
    "write": ((60, 10), (120, 60)),
    "record": ((20, 10), (60, 60)),
    "import": ((2, 60),),
    "directory_bulk": ((2, 60),),
}
GLOBAL_RULES = {
    "login": ((30, 60),),
    "write": ((360, 60),),
    "record": ((180, 60),),
    "import": ((4, 60),),
    "directory_bulk": ((4, 60),),
}


class RateLimitError(StoreError):
    def __init__(self, retry_after, message="提交过于频繁"):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f"{message}，请在 {self.retry_after} 秒后重试。", 429)


class SubmissionGuard:
    def __init__(self, *, clock=time.monotonic, peer_rules=None, global_rules=None, max_clients=256):
        self._clock = clock
        self._peer_rules = PEER_RULES if peer_rules is None else peer_rules
        self._global_rules = GLOBAL_RULES if global_rules is None else global_rules
        self._max_clients = max_clients
        self._clients = OrderedDict()
        self._global = {}
        self._lock = threading.Lock()

    def consume(self, action, peer, *, cost=1):
        if type(cost) is not int or cost < 1:
            raise ValueError("submission cost must be a positive integer")
        if action not in self._peer_rules or action not in self._global_rules:
            raise ValueError("unknown submission action")
        with self._lock:
            now = self._clock()
            client = self._clients.setdefault(peer, {})
            self._clients.move_to_end(peer)
            while len(self._clients) > self._max_clients:
                self._clients.popitem(last=False)
            windows = []
            for bank, rules in ((client, self._peer_rules[action]), (self._global, self._global_rules[action])):
                for index, (limit, seconds) in enumerate(rules):
                    if cost > limit:
                        raise StoreError("单次提交的条目过多，请分批提交。", 413)
                    events = bank.setdefault((action, index), deque())
                    while events and events[0][0] <= now - seconds:
                        events.popleft()
                    windows.append((events, limit, seconds))
            retry = 0
            for events, limit, seconds in windows:
                needed = sum(count for _, count in events) + cost - limit
                if needed > 0:
                    for stamp, count in events:
                        needed -= count
                        if needed <= 0:
                            retry = max(retry, stamp + seconds - now)
                            break
            if retry > 0:
                raise RateLimitError(retry, "登录尝试过于频繁" if action == "login" else "提交过于频繁")
            for events, _, _ in windows:
                events.append((now, cost))


def validate_submission_notes(payload, *, previous_notes=None):
    if not isinstance(payload, dict) or "notes" not in payload:
        return
    notes = payload["notes"]
    if not isinstance(notes, str):
        raise StoreError("备注必须是文字。")
    try:
        notes.encode("utf-8")
    except UnicodeError:
        raise StoreError("备注包含无效文字，请检查输入。") from None
    if "\x00" in notes:
        raise StoreError("备注包含无效字符。")
    if len(notes) > MAX_NOTES_CHARS and notes != previous_notes:
        raise StoreError(f"备注最多 {MAX_NOTES_CHARS} 字。")
