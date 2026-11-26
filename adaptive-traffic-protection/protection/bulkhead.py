"""
Per-application Bulkhead (concurrency admission controller).

Each registered backend application gets its own Bulkhead instance.
The Bulkhead tracks in-flight requests and rejects new requests when the
current concurrency limit is reached.

Design decisions
----------------
- Uses a simple asyncio.Semaphore under the hood but wraps it so the limit
  can be updated at runtime without corrupting requests that are already
  waiting or in-flight.
- When the limit is increased, additional permits become available immediately.
- When the limit is decreased, no existing in-flight requests are killed; the
  lower limit takes effect for new requests.
- The in-flight counter is kept separate from the semaphore so the gateway
  can read it without acquiring the semaphore.
- All limit changes are protected by an asyncio.Lock so concurrent controller
  updates are serialised.

Semaphore replacement strategy (safe update)
--------------------------------------------
asyncio.Semaphore does not support dynamic resizing. We work around this by
maintaining an explicit _in_flight counter and an _effective_limit integer
rather than relying on the semaphore's internal counter. Admission is decided
by comparing _in_flight < _effective_limit without ever blocking on acquire(),
so the limit can be changed atomically under the _lock.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncGenerator

from monitoring.logger import get_logger

logger = get_logger(__name__)


class Bulkhead:
    """
    Per-application concurrency admission controller.

    Usage::

        async with bulkhead.acquire() as admitted:
            if not admitted:
                # Return HTTP 429 — the application is at capacity
                ...
            # Forward the request to the backend
            ...
        # The in-flight count is decremented automatically on exit.
    """

    def __init__(self, app_id: str, initial_limit: int) -> None:
        self.app_id = app_id
        self._lock = asyncio.Lock()
        self._in_flight: int = 0
        self._effective_limit: int = max(1, initial_limit)
        self._rejected: int = 0

    # ── Context manager ───────────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncGenerator[bool, None]:
        """
        Try to admit one request.

        Yields True if admitted, False if rejected (limit reached).
        Automatically decrements the in-flight counter on exit when admitted.
        """
        admitted = False
        async with self._lock:
            if self._in_flight < self._effective_limit:
                self._in_flight += 1
                admitted = True
            else:
                self._rejected += 1

        try:
            yield admitted
        finally:
            if admitted:
                async with self._lock:
                    self._in_flight = max(0, self._in_flight - 1)

    # ── Limit management ──────────────────────────────────────────────────────

    async def update_limit(self, new_limit: int) -> None:
        """
        Update the effective concurrency limit.

        The change takes effect immediately for the next admission decision.
        In-flight requests that are already admitted are not interrupted.
        """
        async with self._lock:
            old_limit = self._effective_limit
            self._effective_limit = max(1, new_limit)
            if self._effective_limit != old_limit:
                logger.debug(
                    "Bulkhead[%s] limit %d → %d",
                    self.app_id, old_limit, self._effective_limit,
                )

    def update_limit_sync(self, new_limit: int) -> None:
        """
        Synchronous limit update for use from non-async controller threads.

        NOTE: This is safe only when the calling code ensures no concurrent
        async update_limit() calls are in progress. In this implementation
        the adaptive controller calls this from a background asyncio task,
        so the event loop serialises it correctly.
        """
        old = self._effective_limit
        self._effective_limit = max(1, new_limit)
        if self._effective_limit != old:
            logger.debug(
                "Bulkhead[%s] limit (sync) %d → %d",
                self.app_id, old, self._effective_limit,
            )

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def in_flight(self) -> int:
        """Current number of admitted in-flight requests."""
        return self._in_flight

    @property
    def effective_limit(self) -> int:
        """Current effective concurrency limit."""
        return self._effective_limit

    @property
    def rejected_total(self) -> int:
        """Total number of rejected requests since startup."""
        return self._rejected

    def is_at_capacity(self) -> bool:
        """Return True if the bulkhead is currently at its limit."""
        return self._in_flight >= self._effective_limit

    def status(self) -> dict:
        """Return a plain-dict snapshot of bulkhead state."""
        return {
            "app_id": self.app_id,
            "in_flight": self._in_flight,
            "effective_limit": self._effective_limit,
            "rejected_total": self._rejected,
            "at_capacity": self.is_at_capacity(),
        }
