"""
Tests for the FastAPI gateway: routing, admission, management endpoints.

Run with:
    pytest tests/test_gateway.py -v
"""

from __future__ import annotations

import json
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient

from gateway.config import (
    AppConfig,
    ControllerSettings,
    GatewayConfig,
    GatewaySettings,
)

# ── Test configuration helpers ────────────────────────────────────────────────

ADMIN_KEY = "test-admin-key"


def make_test_config(mode: str = "adaptive") -> GatewayConfig:
    """Build a minimal GatewayConfig without touching any file."""
    return GatewayConfig(
        applications={
            "app_a": AppConfig(
                name="Test App A",
                base_url="http://localhost:8001",
                initial_concurrency=10,
                min_concurrency=2,
                max_concurrency=50,
            ),
            "app_b": AppConfig(
                name="Test App B",
                base_url="http://localhost:8002",
                initial_concurrency=10,
                min_concurrency=2,
                max_concurrency=50,
            ),
        },
        gateway=GatewaySettings(
            admin_api_key=ADMIN_KEY,
            mode=mode,
            global_concurrency_cap=200,
        ),
        controller=ControllerSettings(
            evaluation_interval_seconds=60.0,  # very long — won't fire during tests
            min_samples_required=5,
        ),
    )


def _ok_response() -> Response:
    """Return a simple 200 response to use as a mock backend reply."""
    return Response(
        content=json.dumps({"ok": True, "app": "mock"}).encode(),
        status_code=200,
        headers={"content-type": "application/json"},
        media_type="application/json",
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
async def gateway_client():
    """
    Provide an async HTTPX client wired to a test gateway app.

    - load_config is patched so no YAML file is needed.
    - GatewayForwarder.forward is patched to return a mock 200 response.
    - AdaptiveController.start/stop are patched so no background task runs.
    """
    cfg = make_test_config()

    with patch("gateway.main.load_config", return_value=cfg), \
         patch("gateway.main.GatewayForwarder.startup", new_callable=AsyncMock), \
         patch("gateway.main.GatewayForwarder.shutdown", new_callable=AsyncMock), \
         patch("gateway.main.GatewayForwarder.forward", new_callable=AsyncMock,
               return_value=_ok_response()) as mock_forward, \
         patch("gateway.main.AdaptiveController.start", new_callable=AsyncMock), \
         patch("gateway.main.AdaptiveController.stop", new_callable=AsyncMock):

        from gateway.main import create_app
        app = create_app()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client, mock_forward


# ── Health and metrics ────────────────────────────────────────────────────────

class TestBasicEndpoints:

    async def test_health_returns_200(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "mode" in body
        assert "applications" in body

    async def test_metrics_returns_prometheus_text(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        # Prometheus text format starts with # HELP or atp_
        assert "atp_" in resp.text or "# HELP" in resp.text

    async def test_unknown_route_returns_404(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get("/api/nonexistent-app/data")
        assert resp.status_code == 404

    async def test_unknown_base_path_not_proxied(self, gateway_client):
        client, _ = gateway_client
        # Non-/api/ path should return 404 (no matching route)
        resp = await client.get("/not-an-api/anything")
        assert resp.status_code == 404


# ── Proxy routing ─────────────────────────────────────────────────────────────

class TestProxyRouting:

    async def test_routes_to_app_a(self, gateway_client):
        client, mock_forward = gateway_client
        resp = await client.get("/api/app-a/data")
        assert resp.status_code == 200
        # Verify forward was called with app_a's backend URL
        call_kwargs = mock_forward.call_args.kwargs
        assert call_kwargs["app_id"] == "app_a"
        assert call_kwargs["backend_path"] == "/data"

    async def test_routes_to_app_b(self, gateway_client):
        client, mock_forward = gateway_client
        resp = await client.get("/api/app-b/data")
        assert resp.status_code == 200
        call_kwargs = mock_forward.call_args.kwargs
        assert call_kwargs["app_id"] == "app_b"
        assert call_kwargs["backend_path"] == "/data"

    async def test_routes_nested_path(self, gateway_client):
        client, mock_forward = gateway_client
        resp = await client.get("/api/app-a/nested/resource/123")
        assert resp.status_code == 200
        call_kwargs = mock_forward.call_args.kwargs
        assert call_kwargs["backend_path"] == "/nested/resource/123"

    async def test_post_method_proxied(self, gateway_client):
        client, mock_forward = gateway_client
        resp = await client.post("/api/app-a/data", json={"key": "value"})
        assert resp.status_code == 200
        assert mock_forward.called

    async def test_root_path_proxied(self, gateway_client):
        client, mock_forward = gateway_client
        resp = await client.get("/api/app-a/")
        assert resp.status_code == 200
        call_kwargs = mock_forward.call_args.kwargs
        assert call_kwargs["backend_path"] == "/"


# ── Admission rejection ───────────────────────────────────────────────────────

class TestAdmissionRejection:

    async def test_rejection_returns_429_when_at_limit(self, gateway_client):
        """
        Fill the app_a bulkhead to its limit (by patching _in_flight) and
        verify the next request gets HTTP 429.
        """
        client, mock_forward = gateway_client

        # Patch the bulkhead for app_a: make it always at capacity
        with patch("protection.bulkhead.Bulkhead.acquire") as mock_acquire:
            # Create a context manager that yields False (rejected)
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _rejected():
                yield False

            mock_acquire.return_value = _rejected()
            resp = await client.get("/api/app-a/data")

        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body


# ── Admin endpoints ───────────────────────────────────────────────────────────

class TestAdminEndpoints:

    async def test_admin_requires_api_key(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get("/admin/applications")
        assert resp.status_code == 403

    async def test_admin_wrong_key_rejected(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get(
            "/admin/applications",
            headers={"X-Api-Key": "wrong-key"},
        )
        assert resp.status_code == 403

    async def test_admin_applications_with_correct_key(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get(
            "/admin/applications",
            headers={"X-Api-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "applications" in body
        ids = {a["app_id"] for a in body["applications"]}
        assert "app_a" in ids
        assert "app_b" in ids

    async def test_admin_controller_status_with_key(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get(
            "/admin/controller/status",
            headers={"X-Api-Key": ADMIN_KEY},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "controller" in body
        assert "recent_history" in body

    async def test_get_mode_no_auth_required(self, gateway_client):
        client, _ = gateway_client
        resp = await client.get("/admin/controller/mode")
        assert resp.status_code == 200
        assert "mode" in resp.json()

    async def test_set_mode_to_static(self, gateway_client):
        client, _ = gateway_client
        resp = await client.post(
            "/admin/controller/mode",
            content=b'{"mode": "static"}',
            headers={"X-Api-Key": ADMIN_KEY, "content-type": "application/json"},
        )
        assert resp.status_code == 200, f"422 body: {resp.text}"
        assert resp.json()["mode"] == "static"

    async def test_set_mode_to_adaptive(self, gateway_client):
        client, _ = gateway_client
        # First switch to static
        await client.post(
            "/admin/controller/mode",
            content=b'{"mode": "static"}',
            headers={"X-Api-Key": ADMIN_KEY, "content-type": "application/json"},
        )
        # Then back to adaptive
        resp = await client.post(
            "/admin/controller/mode",
            content=b'{"mode": "adaptive"}',
            headers={"X-Api-Key": ADMIN_KEY, "content-type": "application/json"},
        )
        assert resp.status_code == 200, f"422 body: {resp.text}"
        assert resp.json()["mode"] == "adaptive"

    async def test_set_invalid_mode_returns_400(self, gateway_client):
        client, _ = gateway_client
        resp = await client.post(
            "/admin/controller/mode",
            content=b'{"mode": "turbo"}',
            headers={"X-Api-Key": ADMIN_KEY, "content-type": "application/json"},
        )
        # FastAPI returns 422 (validation) or 400 (business logic) for invalid mode
        assert resp.status_code in (400, 422), f"Expected 400 or 422, got {resp.status_code}"

    async def test_set_mode_requires_auth(self, gateway_client):
        client, _ = gateway_client
        resp = await client.post(
            "/admin/controller/mode",
            json={"mode": "static"},
        )
        assert resp.status_code == 403
