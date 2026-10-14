"""Thread-safe Redis subset for OAuth tests; real Lua/TTL is tested by the live probe."""

import copy
import threading
import time
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    def __init__(self, *, fail_set: bool = False) -> None:
        self.store: dict[str, Any] = {}
        self.expiry: dict[str, float] = {}
        self.set_calls: list[dict[str, Any]] = []
        self.fail_set = fail_set
        self.fail_eval = False
        self.fail_ping = False
        self._mutex = threading.RLock()

    def ping(self) -> bool:
        if self.fail_ping:
            raise RedisConnectionError("unavailable")
        return True

    def exists(self, key: str) -> bool:
        with self._mutex:
            if key in self.expiry and time.monotonic() >= self.expiry[key]:
                self.store.pop(key, None)
            return key in self.store

    def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool:
        with self._mutex:
            self.set_calls.append({"key": key, "nx": nx, "px": px})
            if self.fail_set:
                raise RedisConnectionError("unavailable")
            if nx and self.exists(key):
                return False
            self.store[key] = value
            if px is not None:
                self.expiry[key] = time.monotonic() + px / 1000
            return True

    def eval(self, script: str, numkeys: int, key: str, *args: Any) -> Any:
        with self._mutex:
            if self.fail_eval:
                raise RedisConnectionError("unavailable")
            self.exists(key)
            if "HGETALL" in script:
                row = self.store.get(key, {})
                if row.get("state") != args[0] or row.get("status") != args[1]:
                    return []
                self.store.pop(key)
                return [item for pair in row.items() for item in pair]
            if self.store.get(key) != args[0]:
                return 0
            if "pexpire" in script:
                self.expiry[key] = time.monotonic() + int(args[1]) / 1000
            else:
                self.store.pop(key)
            return 1

    def hgetall(self, key: str) -> dict[str, str]:
        with self._mutex:
            return copy.deepcopy(self.store.get(key, {})) if self.exists(key) else {}

    def hmset(self, key: str, data: dict[str, str]) -> None:
        if self.fail_set:
            raise RedisConnectionError("unavailable")
        self.store[key] = copy.deepcopy(data)

    def expire(self, key: str, ttl: int) -> None:
        self.expiry[key] = time.monotonic() + ttl

    def delete(self, key: str) -> int:
        return int(self.store.pop(key, None) is not None)

    def scan_iter(self, *, match: str, count: int = 100):
        return iter([key for key in self.store if key.startswith(match.rstrip("*"))])

    def pipeline(self, transaction: bool = True):
        redis = self

        class Pipeline:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                def queue(*args, **kwargs):
                    self.calls.append((name, args, kwargs))
                    return self

                return queue

            def execute(self):
                with redis._mutex:
                    return [getattr(redis, name)(*args, **kwargs) for name, args, kwargs in self.calls]

        return Pipeline()
