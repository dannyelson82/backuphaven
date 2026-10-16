"""Login throttling: exponential delay per username and per IP (SECURITY.md)."""

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

FREE_ATTEMPTS = 3  # failures allowed before any delay
MAX_DELAY_S = 900.0
_MAX_ENTRIES = 10_000


def delay_for(failures: int) -> float:
    if failures < FREE_ATTEMPTS:
        return 0.0
    return min(2.0 ** (failures - FREE_ATTEMPTS + 1), MAX_DELAY_S)


@dataclass
class _Entry:
    failures: int
    last: float


class LoginThrottle:
    """In memory; a restart clears it, which is acceptable for a home server."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def retry_after(self, keys: Iterable[str]) -> float:
        """Seconds until another attempt is allowed (0 = allowed now)."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key in keys:
                entry = self._entries.get(key)
                if entry is not None:
                    wait = max(wait, entry.last + delay_for(entry.failures) - now)
        return max(wait, 0.0)

    def failure(self, keys: Iterable[str]) -> list[str]:
        """Record a failed attempt. Returns keys that just became throttled."""
        now = self._clock()
        newly_locked: list[str] = []
        with self._lock:
            if len(self._entries) > _MAX_ENTRIES:
                self._prune(now)
            for key in keys:
                entry = self._entries.setdefault(key, _Entry(0, now))
                entry.failures += 1
                entry.last = now
                if entry.failures == FREE_ATTEMPTS:
                    newly_locked.append(key)
        return newly_locked

    def success(self, keys: Iterable[str]) -> None:
        with self._lock:
            for key in keys:
                self._entries.pop(key, None)

    def _prune(self, now: float) -> None:
        stale = [k for k, e in self._entries.items() if now - e.last > 2 * MAX_DELAY_S]
        for key in stale:
            del self._entries[key]
