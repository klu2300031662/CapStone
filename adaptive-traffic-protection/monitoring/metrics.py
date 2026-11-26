"""
Prometheus metrics definitions for the Adaptive Traffic Protection Platform.

This module defines all Prometheus counters, gauges, and histograms used
across the platform. Metrics are labelled by application so each registered
backend can be monitored independently.
"""

from prometheus_client import Counter, Gauge, Histogram, Info

# ── Request counters ──────────────────────────────────────────────────────────

REQUESTS_TOTAL = Counter(
    "atp_requests_total",
    "Total number of requests received by the gateway, labelled by application and HTTP method.",
    ["app_id", "method"],
)

REQUESTS_SUCCESS = Counter(
    "atp_requests_success_total",
    "Total number of requests successfully forwarded to a backend application.",
    ["app_id"],
)

REQUESTS_FAILED = Counter(
    "atp_requests_failed_total",
    "Total number of requests that resulted in a backend error (5xx or connection failure).",
    ["app_id"],
)

REQUESTS_REJECTED = Counter(
    "atp_requests_rejected_total",
    "Total number of requests rejected by the admission controller (HTTP 429).",
    ["app_id"],
)

REQUESTS_TIMEOUT = Counter(
    "atp_requests_timeout_total",
    "Total number of requests that timed out waiting for the backend.",
    ["app_id"],
)

# ── Latency histogram ─────────────────────────────────────────────────────────

REQUEST_LATENCY = Histogram(
    "atp_request_latency_seconds",
    "End-to-end request latency as measured by the gateway (seconds).",
    ["app_id"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── In-flight / concurrency gauges ────────────────────────────────────────────

IN_FLIGHT_REQUESTS = Gauge(
    "atp_in_flight_requests",
    "Number of requests currently being processed for this application.",
    ["app_id"],
)

CONCURRENCY_LIMIT = Gauge(
    "atp_concurrency_limit",
    "Current adaptive concurrency limit for this application.",
    ["app_id"],
)

# ── Controller metrics ────────────────────────────────────────────────────────

CONTROLLER_UPDATES = Counter(
    "atp_controller_updates_total",
    "Number of times the adaptive controller has updated the concurrency limit.",
    ["app_id", "direction"],  # direction: "increase" | "decrease" | "unchanged"
)

CONTROLLER_MODE = Gauge(
    "atp_controller_mode",
    "Current operating mode: 1 = adaptive, 0 = static.",
    [],
)

# ── Platform info ─────────────────────────────────────────────────────────────

PLATFORM_INFO = Info(
    "atp_platform",
    "Metadata about the Adaptive Traffic Protection platform.",
)

PLATFORM_INFO.info({
    "version": "1.0.0",
    "algorithm": "AIMD",
    "description": "Adaptive Traffic Protection Without Static Rate Limits",
})
