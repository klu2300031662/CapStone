"""
Admission controller façade.

The AdmissionController wraps the per-application Bulkhead and the global
rate limiter into a single interface used by the gateway's request handler.
It also updates Prometheus gauges and the metrics collector.
"""

from __future__ import annotations

import contextlib
from typing import AsyncGenerator, Tuple

from monitoring.logger import get_logger
from monitoring.metrics import IN_FLIGHT_REQUESTS, REQUESTS_REJECTED
from protection.bulkhead import Bulkhead
from protection.rate_limiter import GlobalRateLimiter

logger = get_logger(__name__)


class AdmissionController:
    """
    Wraps the global limiter and per-application bulkhead.

    Usage::

        async with admission.try_admit(app_id, bulkhead, metrics) as admitted:
            if not admitted:
                return JSONResponse({"error": "Too many requests"}, 429)
            # forward request
    """

    def __init__(self, global_limiter: GlobalRateLimiter) -> None:
        self._global = global_limiter

    @contextlib.asynccontextmanager
    async def try_admit(
        self,
        app_id: str,
        bulkhead: Bulkhead,
        metrics_collector,  # monitoring.collector.MetricsCollector
    ) -> AsyncGenerator[bool, None]:
        """
        Try to admit a request through the global limiter and app bulkhead.

        Yields True if both checks pass, False if either rejects.
        Cleans up in-flight tracking on exit.
        """
        # Check global cap first (cheapest check)
        async with self._global.acquire() as global_ok:
            if not global_ok:
                logger.warning("[%s] Global concurrency cap exceeded.", app_id)
                REQUESTS_REJECTED.labels(app_id=app_id).inc()
                metrics_collector.record_rejection()
                yield False
                return

            # Check per-application bulkhead
            async with bulkhead.acquire() as app_ok:
                if not app_ok:
                    logger.info(
                        "[%s] Application concurrency limit reached (limit=%d, in_flight=%d).",
                        app_id, bulkhead.effective_limit, bulkhead.in_flight,
                    )
                    REQUESTS_REJECTED.labels(app_id=app_id).inc()
                    metrics_collector.record_rejection()
                    yield False
                    return

                # Admitted — update Prometheus gauge
                IN_FLIGHT_REQUESTS.labels(app_id=app_id).set(bulkhead.in_flight)
                metrics_collector.set_in_flight(bulkhead.in_flight)

                try:
                    yield True
                finally:
                    IN_FLIGHT_REQUESTS.labels(app_id=app_id).set(bulkhead.in_flight)
                    metrics_collector.set_in_flight(bulkhead.in_flight)
