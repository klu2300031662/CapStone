"""
Per-application metrics collector.

Each registered backend application gets its own MetricsCollector instance.
The collector maintains a rolling time window of observations used by the
adaptive controller to decide whether to increase or decrease the concurrency
limit.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional


@dataclass
class RequestSample:
    """A single recorded request observation."""
    timestamp: float          # Unix timestamp when the request completed
    latency_ms: float         # End-to-end latency in milliseconds
    success: bool             # True = 2xx/3xx, False = 4xx/5xx/timeout
    timed_out: bool = False   # True if the backend did not respond in time


@dataclass
class AppMetricSnapshot:
    """A point-in-time snapshot of application metrics."""
    app_id: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    rejected_requests: int
    timeout_requests: int
    in_flight: int
    concurrency_limit: int
    avg_latency_ms: float
    p95_latency_ms: float
    error_rate: float          # 0.0 – 1.0
    requests_per_second: float
    window_sample_count: int


class MetricsCollector:
    """
    Thread-safe rolling-window metrics collector for a single backend application.

    Uses a deque bounded by time to store recent request samples. All public
    methods are protected by a reentrant lock so they are safe to call from
    concurrent asyncio tasks (via run_in_executor) or from background threads.
    """

    def __init__(self, app_id: str, window_seconds: float = 30.0) -> None:
        self.app_id = app_id
        self.window_seconds = window_seconds
        self._lock = threading.RLock()

        # Rolling window of recent samples
        self._samples: Deque[RequestSample] = deque()

        # Lifetime counters (never reset)
        self._total_requests: int = 0
        self._successful_requests: int = 0
        self._failed_requests: int = 0
        self._rejected_requests: int = 0
        self._timeout_requests: int = 0

        # Current state (updated externally by the gateway)
        self._in_flight: int = 0
        self._concurrency_limit: int = 0

    # ── Recording methods ─────────────────────────────────────────────────────

    def record_request(self, latency_ms: float, success: bool, timed_out: bool = False) -> None:
        """Record a completed request observation."""
        with self._lock:
            now = time.monotonic()
            sample = RequestSample(
                timestamp=now,
                latency_ms=latency_ms,
                success=success,
                timed_out=timed_out,
            )
            self._samples.append(sample)
            self._total_requests += 1
            if success:
                self._successful_requests += 1
            else:
                self._failed_requests += 1
            if timed_out:
                self._timeout_requests += 1
            self._evict_old_samples(now)

    def record_rejection(self) -> None:
        """Record that a request was rejected by the admission controller."""
        with self._lock:
            self._rejected_requests += 1
            self._total_requests += 1

    def set_in_flight(self, count: int) -> None:
        """Update the current in-flight request count."""
        with self._lock:
            self._in_flight = max(0, count)

    def set_concurrency_limit(self, limit: int) -> None:
        """Update the current concurrency limit (called after each controller cycle)."""
        with self._lock:
            self._concurrency_limit = limit

    # ── Query methods ─────────────────────────────────────────────────────────

    def get_window_samples(self) -> List[RequestSample]:
        """Return a copy of samples within the current rolling window."""
        with self._lock:
            now = time.monotonic()
            self._evict_old_samples(now)
            return list(self._samples)

    def get_snapshot(self) -> AppMetricSnapshot:
        """Return a point-in-time snapshot of all metrics for this application."""
        with self._lock:
            now = time.monotonic()
            self._evict_old_samples(now)
            samples = list(self._samples)

        latencies = [s.latency_ms for s in samples]
        avg_latency = _mean(latencies)
        p95_latency = _percentile(latencies, 95)
        total_in_window = len(samples)
        failed_in_window = sum(1 for s in samples if not s.success)
        error_rate = (failed_in_window / total_in_window) if total_in_window > 0 else 0.0

        # Requests per second over the window
        rps = total_in_window / self.window_seconds if self.window_seconds > 0 else 0.0

        with self._lock:
            return AppMetricSnapshot(
                app_id=self.app_id,
                total_requests=self._total_requests,
                successful_requests=self._successful_requests,
                failed_requests=self._failed_requests,
                rejected_requests=self._rejected_requests,
                timeout_requests=self._timeout_requests,
                in_flight=self._in_flight,
                concurrency_limit=self._concurrency_limit,
                avg_latency_ms=avg_latency,
                p95_latency_ms=p95_latency,
                error_rate=error_rate,
                requests_per_second=rps,
                window_sample_count=total_in_window,
            )

    def get_total_counts(self) -> dict:
        """Return lifetime counters as a plain dict."""
        with self._lock:
            return {
                "total": self._total_requests,
                "success": self._successful_requests,
                "failed": self._failed_requests,
                "rejected": self._rejected_requests,
                "timeout": self._timeout_requests,
            }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _evict_old_samples(self, now: float) -> None:
        """Remove samples older than the rolling window. Must be called under lock."""
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()


def _mean(values: List[float]) -> float:
    """Return arithmetic mean or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _percentile(values: List[float], pct: int) -> float:
    """Return the pct-th percentile of values, or 0.0 for an empty list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    index = int(len(sorted_vals) * pct / 100)
    index = min(index, len(sorted_vals) - 1)
    return sorted_vals[index]
