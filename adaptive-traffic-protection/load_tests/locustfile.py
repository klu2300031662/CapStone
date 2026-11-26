"""
Locust load-testing script for Adaptive Traffic Protection platform.

Scenarios
---------
  MixedTrafficUser  — balanced load across App A and App B
  AppAUser          — targets only App A (baseline / healthy service)
  AppBUser          — targets only App B (can be stressed via admin API)
  OverloadUser      — hammers App B to trigger overload + adaptive decrease

Usage
-----
  # Interactive web UI (default):
  locust -f load_tests/locustfile.py --host http://localhost:8000

  # Headless with 50 users, spawn rate 5/s, run for 60 seconds:
  locust -f load_tests/locustfile.py --host http://localhost:8000 \\
         --headless -u 50 -r 5 --run-time 60s

  # Compare modes:
  #   1. Set gateway to static mode:
  #      Invoke-RestMethod -Uri http://localhost:8000/admin/controller/mode \\
  #        -Method POST -Headers @{"X-Api-Key"="gateway-admin-secret-key-change-in-production"} \\
  #        -Body '{"mode":"static"}' -ContentType "application/json"
  #   2. Run Locust, record metrics.
  #   3. Set gateway to adaptive mode and re-run.

  # Simulate App B overload:
  #   Invoke-RestMethod -Uri http://localhost:8002/admin/delay \\
  #     -Method POST -Headers @{"X-Admin-Key"="app-b-admin-key"} \\
  #     -Body '{"delay_ms":2000}' -ContentType "application/json"

Metrics to observe
------------------
  - Requests per second (throughput)
  - Median and 95th-percentile response time
  - Failure rate (includes HTTP 429 rejections)
  - Gateway /metrics endpoint: atp_concurrency_limit{app_id="app_b"}
"""

from __future__ import annotations

import logging
import os
import random

import requests as _requests
from locust import HttpUser, between, events, task

logger = logging.getLogger("locust.atp")

# ── Configuration ─────────────────────────────────────────────────────────────

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "http://localhost:8000")
APP_B_HOST = os.getenv("APP_B_HOST", "http://localhost:8002")
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "gateway-admin-secret-key-change-in-production")
APP_B_ADMIN_KEY = os.getenv("APP_B_ADMIN_KEY", "app-b-admin-key")

# Track 429 rejections separately so they don't inflate Locust's failure count
_rejection_count = 0
_total_count = 0


# ── Event hooks ───────────────────────────────────────────────────────────────

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("  Adaptive Traffic Protection — Load Test Starting")
    print("=" * 60)
    print(f"  Gateway:        {GATEWAY_HOST}")
    print(f"  App B (direct): {APP_B_HOST}")
    print()
    print("  Endpoints under test:")
    print("    GET /api/app-a/data   — App A normal endpoint")
    print("    GET /api/app-b/data   — App B (may be stressed)")
    print("    GET /api/app-a/slow   — App A slow endpoint")
    print("    GET /api/app-b/slow   — App B slow endpoint")
    print()
    print("  Tip: To stress App B, POST to http://localhost:8002/admin/delay")
    print("       with body {\"delay_ms\": 2000} and X-Admin-Key header.")
    print("=" * 60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    total = _total_count or 1
    rejection_pct = (_rejection_count / total) * 100
    print(f"\n  Rejection summary: {_rejection_count}/{total} requests returned 429 ({rejection_pct:.1f}%)\n")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response,
               context, exception, **kwargs):
    global _rejection_count, _total_count
    _total_count += 1
    if hasattr(response, "status_code") and response.status_code == 429:
        _rejection_count += 1


# ── Helper ────────────────────────────────────────────────────────────────────

def _set_app_b_delay(delay_ms: int) -> bool:
    """POST to App B admin endpoint to change its response delay."""
    try:
        r = _requests.post(
            f"{APP_B_HOST}/admin/delay",
            json={"delay_ms": delay_ms},
            headers={"X-Admin-Key": APP_B_ADMIN_KEY},
            timeout=5,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("Could not set App B delay: %s", exc)
        return False


def _set_app_b_failure(failure_rate: float, enabled: bool = True) -> bool:
    """POST to App B admin endpoint to configure failure injection."""
    try:
        r = _requests.post(
            f"{APP_B_HOST}/admin/failure",
            json={"failure_rate": failure_rate, "enabled": enabled},
            headers={"X-Admin-Key": APP_B_ADMIN_KEY},
            timeout=5,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("Could not set App B failure rate: %s", exc)
        return False


def _handle_response(response, name: str) -> None:
    """Mark 429 as a 'soft' failure with a custom label (not Locust failure)."""
    if response.status_code == 429:
        # Don't mark as Locust failure; track separately
        response.success()
        logger.debug("429 rejection on %s", name)
    elif response.status_code >= 500:
        response.failure(f"Server error {response.status_code}")


# ── User classes ──────────────────────────────────────────────────────────────

class MixedTrafficUser(HttpUser):
    """
    Sends a balanced mix of requests to both App A and App B.
    Good for observing how the adaptive controller keeps both apps healthy.
    """

    wait_time = between(0.1, 0.5)

    @task(3)
    def app_a_data(self):
        with self.client.get("/api/app-a/data", catch_response=True, name="app_a /data") as r:
            _handle_response(r, "app_a /data")

    @task(2)
    def app_b_data(self):
        with self.client.get("/api/app-b/data", catch_response=True, name="app_b /data") as r:
            _handle_response(r, "app_b /data")

    @task(1)
    def app_a_slow(self):
        with self.client.get("/api/app-a/slow", catch_response=True, name="app_a /slow") as r:
            _handle_response(r, "app_a /slow")

    @task(1)
    def app_b_slow(self):
        with self.client.get("/api/app-b/slow", catch_response=True, name="app_b /slow") as r:
            _handle_response(r, "app_b /slow")

    @task(1)
    def gateway_health(self):
        with self.client.get("/health", catch_response=True, name="gateway /health") as r:
            if r.status_code != 200:
                r.failure(f"Health check failed: {r.status_code}")


class AppAUser(HttpUser):
    """
    Targets only App A — demonstrates a healthy, unaffected application
    even when App B is overloaded.
    """

    wait_time = between(0.05, 0.3)

    @task(4)
    def data(self):
        with self.client.get("/api/app-a/data", catch_response=True, name="app_a /data") as r:
            _handle_response(r, "app_a /data")

    @task(1)
    def slow(self):
        with self.client.get("/api/app-a/slow", catch_response=True, name="app_a /slow") as r:
            _handle_response(r, "app_a /slow")


class AppBUser(HttpUser):
    """
    Targets only App B. Use the on_start hook to optionally increase delay.
    Demonstrates how the adaptive controller reduces App B's limit when stressed.
    """

    wait_time = between(0.05, 0.3)

    @task(4)
    def data(self):
        with self.client.get("/api/app-b/data", catch_response=True, name="app_b /data") as r:
            _handle_response(r, "app_b /data")

    @task(1)
    def slow(self):
        with self.client.get("/api/app-b/slow", catch_response=True, name="app_b /slow") as r:
            _handle_response(r, "app_b /slow")


class OverloadUser(HttpUser):
    """
    Very aggressive user targeting App B to trigger overload.

    This user hammers App B at a high rate to demonstrate:
    1. HTTP 429 responses when the bulkhead is at capacity.
    2. Adaptive controller decreasing App B's concurrency limit.
    3. App A remaining unaffected (run AppAUser in parallel).
    """

    wait_time = between(0.01, 0.05)   # very short wait — aggressive load

    def on_start(self):
        """Increase App B delay when this user starts to simulate a slow backend."""
        success = _set_app_b_delay(1500)
        if success:
            logger.info("OverloadUser: App B delay set to 1500ms")
        else:
            logger.warning("OverloadUser: Could not set App B delay (direct access unavailable)")

    def on_stop(self):
        """Reset App B delay when test ends."""
        _set_app_b_delay(50)
        _set_app_b_failure(0.0, enabled=False)

    @task(5)
    def hammer_data(self):
        with self.client.get("/api/app-b/data", catch_response=True, name="app_b /data [overload]") as r:
            _handle_response(r, "app_b /data [overload]")

    @task(2)
    def hammer_slow(self):
        with self.client.get("/api/app-b/slow", catch_response=True, name="app_b /slow [overload]") as r:
            _handle_response(r, "app_b /slow [overload]")

    @task(1)
    def inject_failure(self):
        """Randomly enable failure injection to simulate backend errors."""
        if random.random() < 0.1:   # 10% chance each task
            _set_app_b_failure(failure_rate=0.3, enabled=True)
        with self.client.get("/api/app-b/data", catch_response=True, name="app_b /data [failure]") as r:
            _handle_response(r, "app_b /data [failure]")
