"""
Gateway-wide safety rate limiter.

This module provides a simple global concurrency cap that prevents the total
number of in-flight requests across ALL applications from exceeding a
configured maximum. This is a last-resort safety net — normal per-application
bulkheads handle the admission logic.

This is NOT a per-second rate limiter. The platform is designed around
concurrent-request limiting, not requests-per-second limiting.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncGenerator

from monitoring.logger import get_logger

logger = get_logger(__name__)


class GlobalRateLimiter:
    """
    Coarse-grained global concurrency cap.

    Rejects any request (with 503) when the total number of in-flight requests
    across all applications would exceed global_cap. This protects gateway
    resources (file descriptors, memory) from being exhausted.
    """

    def __init__(self, global_cap: int) -> None:
        self._global_cap = max(1, global_cap)
        self._in_flight: int = 0
        self._lock = asyncio.Lock()
        self._rejected: int = 0

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncGenerator[bool, None]:
        """
        Try to admit one request under the global cap.

        Yields True if admitted, False if the global cap is reached.
        """
        admitted = False
        async with self._lock:
            if self._in_flight < self._global_cap:
                self._in_flight += 1
                admitted = True
            else:
                self._rejected += 1
                logger.warning(
                    "Global concurrency cap reached (%d/%d).",
                    self._in_flight, self._global_cap,
                )
        try:
            yield admitted
        finally:
            if admitted:
                async with self._lock:
                    self._in_flight = max(0, self._in_flight - 1)

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def global_cap(self) -> int:
        return self._global_cap

    def status(self) -> dict:
        return {
            "global_cap": self._global_cap,
            "in_flight": self._in_flight,
            "rejected_total": self._rejected,
        }
