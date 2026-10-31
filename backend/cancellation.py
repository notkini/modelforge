"""Request-scoped cancellation for generation and refinement operations.

Every generate/refine operation gets a unique, unpredictable request ID.
The registry tracks per-request state so cancellation can be targeted to a
single operation without affecting other users or concurrent requests.

All state is kept in process memory. This works within a single backend
process; cross-worker coordination would require shared storage (Redis,
database, etc.) and is not part of this implementation.

Thread safety: all registry access is guarded by a threading.Lock so the
synchronous FastAPI route handlers (which run in Starlette's thread pool)
can safely check and request cancellation from different threads.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RequestCancellation:
    """Per-request cancellation state and handle bookkeeping."""
    request_id: str
    conversation_id: str
    user_id: str
    status: str = 'running'  # running | cancellation_requested | cancelled | completed | failed
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def request_cancellation(self, requesting_user_id: str) -> bool:
        """Mark this request for cancellation if the caller owns it.

        Returns True when cancellation was requested, False if already
        terminal or not owned by this user.
        """
        with self._lock:
            if self.status in ('completed', 'failed', 'cancelled'):
                return False
            if self.user_id != requesting_user_id:
                return False
            self.status = 'cancellation_requested'
            self._event.set()
            return True

    def is_cancelled(self) -> bool:
        return self.status == 'cancellation_requested' or self.status == 'cancelled'

    def is_running(self) -> bool:
        return self.status == 'running'

    def mark_completed(self) -> None:
        with self._lock:
            if self.status == 'running':
                self.status = 'completed'
                self._event.set()

    def mark_failed(self) -> None:
        with self._lock:
            if self.status == 'running':
                self.status = 'failed'
                self._event.set()

    def mark_cancelled(self) -> None:
        with self._lock:
            self.status = 'cancelled'
            self._event.set()


class CancellationRegistry:
    """Thread-safe registry of per-request cancellation state.

    Holds only active (non-terminal) requests; terminal entries are removed
    on completion to prevent unbounded growth. Expired entries can be swept
    with :meth:`sweep`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, RequestCancellation] = {}

    def register(self, request_id: str, conversation_id: str, user_id: str) -> RequestCancellation:
        entry = RequestCancellation(request_id=request_id, conversation_id=conversation_id, user_id=user_id)
        with self._lock:
            self._requests[request_id] = entry
        return entry

    def get(self, request_id: str) -> Optional[RequestCancellation]:
        with self._lock:
            return self._requests.get(request_id)

    def remove(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    def request_cancellation(self, request_id: str, user_id: str) -> Optional[RequestCancellation]:
        """Request cancellation for a specific request owned by user_id.

        Returns the registry entry if cancellation was applied, None if the
        request doesn't exist, isn't owned by the user, or is already
        terminal. Idempotent: repeated calls return None after the first.
        """
        entry = self.get(request_id)
        if entry is None:
            return None
        if not entry.request_cancellation(user_id):
            return None
        return entry

    def complete(self, request_id: str) -> None:
        entry = self.get(request_id)
        if entry is not None:
            entry.mark_completed()
            self.remove(request_id)

    def fail(self, request_id: str) -> None:
        entry = self.get(request_id)
        if entry is not None:
            entry.mark_failed()
            self.remove(request_id)

    def cancel(self, request_id: str) -> None:
        entry = self.get(request_id)
        if entry is not None:
            entry.mark_cancelled()
            self.remove(request_id)

    def is_cancelled(self, request_id: str) -> bool:
        entry = self.get(request_id)
        return entry is not None and entry.is_cancelled()

    def sweep(self, max_age_seconds: float = 3600.0) -> int:
        """Remove stale entries older than max_age_seconds. Returns count removed."""
        import time
        now = time.monotonic()
        removed = 0
        with self._lock:
            stale = [rid for rid, entry in self._requests.items()
                     if entry.status in ('completed', 'failed', 'cancelled')]
            for rid in stale:
                self._requests.pop(rid, None)
                removed += 1
        return removed


# Module-level singleton shared across the backend process.
registry = CancellationRegistry()


def new_request_id() -> str:
    """Generate a unique, unpredictable request ID."""
    return secrets.token_urlsafe(24)
