"""
Core gateway request forwarder.

Accepts an admitted HTTP request, forwards it to the backend application
using HTTPX, records latency and success/failure, and returns the backend
response to the client.

This module is intentionally separate from FastAPI route handling (main.py)
so it can be unit-tested without spinning up an HTTP server.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from monitoring.collector import MetricsCollector
from monitoring.logger import get_logger
from monitoring.metrics import (
    REQUEST_LATENCY,
    REQUESTS_FAILED,
    REQUESTS_SUCCESS,
    REQUESTS_TIMEOUT,
    REQUESTS_TOTAL,
)

logger = get_logger(__name__)

# Headers that must not be blindly forwarded (hop-by-hop and gateway internals)
_HOP_BY_HOP_HEADERS = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
    "host",  # will be set by httpx to the backend host
])


class GatewayForwarder:
    """
    Forwards admitted requests to backend applications and records metrics.

    One shared GatewayForwarder instance is used for all applications.
    It holds a single async HTTPX client that reuses connections efficiently.
    """

    def __init__(self, default_timeout: float = 10.0) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._default_timeout = default_timeout

    async def startup(self) -> None:
        """Create the shared HTTPX client. Must be called once at gateway startup."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._default_timeout),
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=500,
                max_keepalive_connections=100,
                keepalive_expiry=30,
            ),
        )
        logger.info("HTTPX client initialised (timeout=%.1fs).", self._default_timeout)

    async def shutdown(self) -> None:
        """Close the shared HTTPX client. Must be called at gateway shutdown."""
        if self._client:
            await self._client.aclose()
            logger.info("HTTPX client closed.")

    async def forward(
        self,
        *,
        app_id: str,
        backend_url: str,
        backend_path: str,
        request: Request,
        timeout_seconds: float,
        metrics: MetricsCollector,
    ) -> Response:
        """
        Forward an HTTP request to the backend and return the response.

        Parameters
        ----------
        app_id:
            Registry identifier — used for metrics labels.
        backend_url:
            Base URL of the backend (e.g. "http://localhost:8001").
        backend_path:
            Path to append to the backend URL (e.g. "/data").
        request:
            The incoming FastAPI Request object.
        timeout_seconds:
            Per-request timeout for this application.
        metrics:
            MetricsCollector instance for this application.

        Returns
        -------
        A FastAPI Response that can be returned directly from a route handler.
        """
        assert self._client is not None, "GatewayForwarder.startup() was not called."

        # Build the full backend URL including query string
        target_url = backend_url.rstrip("/") + backend_path
        query = str(request.url.query)
        if query:
            target_url += "?" + query

        # Filter headers
        forward_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }

        # Read request body (may be empty for GET)
        body = await request.body()

        REQUESTS_TOTAL.labels(app_id=app_id, method=request.method).inc()

        start_time = time.monotonic()
        try:
            backend_response = await self._client.request(
                method=request.method,
                url=target_url,
                headers=forward_headers,
                content=body,
                timeout=timeout_seconds,
            )
            latency_s = time.monotonic() - start_time
            latency_ms = latency_s * 1000.0

            success = backend_response.status_code < 500
            metrics.record_request(latency_ms=latency_ms, success=success)
            REQUEST_LATENCY.labels(app_id=app_id).observe(latency_s)

            if success:
                REQUESTS_SUCCESS.labels(app_id=app_id).inc()
            else:
                REQUESTS_FAILED.labels(app_id=app_id).inc()

            logger.debug(
                "[%s] %s %s → %d (%.1fms)",
                app_id, request.method, target_url, backend_response.status_code, latency_ms,
            )

            # Stream the response back to the client
            return Response(
                content=backend_response.content,
                status_code=backend_response.status_code,
                headers=dict(backend_response.headers),
                media_type=backend_response.headers.get("content-type"),
            )

        except httpx.TimeoutException:
            latency_s = time.monotonic() - start_time
            latency_ms = latency_s * 1000.0
            logger.warning("[%s] Backend request timed out after %.1fms.", app_id, latency_ms)
            metrics.record_request(latency_ms=latency_ms, success=False, timed_out=True)
            REQUEST_LATENCY.labels(app_id=app_id).observe(latency_s)
            REQUESTS_FAILED.labels(app_id=app_id).inc()
            REQUESTS_TIMEOUT.labels(app_id=app_id).inc()
            return JSONResponse(
                status_code=504,
                content={
                    "error": "Gateway Timeout",
                    "detail": f"Backend application '{app_id}' did not respond within {timeout_seconds}s.",
                },
            )

        except httpx.ConnectError:
            latency_s = time.monotonic() - start_time
            latency_ms = latency_s * 1000.0
            logger.error("[%s] Cannot connect to backend at %s.", app_id, backend_url)
            metrics.record_request(latency_ms=latency_ms, success=False)
            REQUESTS_FAILED.labels(app_id=app_id).inc()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service Unavailable",
                    "detail": f"Cannot connect to backend application '{app_id}'.",
                },
            )

        except Exception as exc:  # noqa: BLE001
            latency_s = time.monotonic() - start_time
            latency_ms = latency_s * 1000.0
            logger.exception("[%s] Unexpected forwarding error: %s", app_id, exc)
            metrics.record_request(latency_ms=latency_ms, success=False)
            REQUESTS_FAILED.labels(app_id=app_id).inc()
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Gateway Error",
                    "detail": "An unexpected error occurred while forwarding the request.",
                },
            )
