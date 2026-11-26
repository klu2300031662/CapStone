"""
Central API Gateway — entry point.

Starts the FastAPI application, registers all routes, initialises all
platform components, and manages startup/shutdown lifecycle.

Port: 8000 (configurable via GATEWAY_PORT env var)

Routes
------
  GET  /health                       — gateway health
  GET  /metrics                      — Prometheus metrics
  GET  /admin/applications           — list registered applications
  GET  /admin/controller/status      — controller status
  GET  /admin/controller/mode        — current mode
  POST /admin/controller/mode        — switch mode
  GET  /api/{app_slug}/{path:path}   — proxy GET to backend
  POST /api/{app_slug}/{path:path}   — proxy POST to backend
  PUT  /api/{app_slug}/{path:path}   — proxy PUT to backend
  DELETE /api/{app_slug}/{path:path} — proxy DELETE to backend
  PATCH  /api/{app_slug}/{path:path} — proxy PATCH to backend
"""

from __future__ import annotations

import contextlib
import os
import time

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from controller.adaptive_controller import AdaptiveController
from gateway.admission import AdmissionController
from gateway.config import load_config
from gateway.gateway import GatewayForwarder
from gateway.registry import ApplicationRegistry
from gateway.router import RequestRouter
from monitoring.logger import get_logger
from monitoring.metrics import CONCURRENCY_LIMIT, CONTROLLER_MODE
from protection.bulkhead import Bulkhead
from protection.rate_limiter import GlobalRateLimiter

logger = get_logger(__name__)


# ── Request/response models (must be module-level for FastAPI schema generation) ──

class ModeRequest(BaseModel):
    mode: str


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create and configure the FastAPI gateway application."""

    config = load_config()
    gw = config.gateway

    # ── Platform components ───────────────────────────────────────────────────

    registry = ApplicationRegistry(config)
    router = RequestRouter(registry)
    global_limiter = GlobalRateLimiter(gw.global_concurrency_cap)
    admission = AdmissionController(global_limiter)
    forwarder = GatewayForwarder(default_timeout=10.0)
    controller = AdaptiveController(registry, config.controller, mode=gw.mode)

    # Create and register one Bulkhead per application
    bulkheads: dict[str, Bulkhead] = {}
    for state in registry.get_all():
        bh = Bulkhead(state.app_id, state.current_limit)
        bulkheads[state.app_id] = bh
        registry.set_bulkhead(state.app_id, bh)
        CONCURRENCY_LIMIT.labels(app_id=state.app_id).set(state.current_limit)

    # Store startup time for the health endpoint
    _startup_time = time.time()

    # ── Admin auth dependency ─────────────────────────────────────────────────

    def require_admin(x_api_key: str = Header(default="")) -> None:
        """Reject requests that do not carry the correct admin API key."""
        if x_api_key != gw.admin_api_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing X-Api-Key header.",
            )

    # ── Lifespan context manager (replaces deprecated on_event) ───────────────

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        await forwarder.startup()
        await controller.start()
        logger.info("Gateway started. Registered applications: %s", registry.list_ids())
        yield
        # Shutdown
        await controller.stop()
        await forwarder.shutdown()
        logger.info("Gateway shut down cleanly.")

    app = FastAPI(
        title="Adaptive Traffic Protection Gateway",
        description="Reusable multi-application adaptive concurrency protection platform.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health endpoint ───────────────────────────────────────────────────────

    @app.get("/health", tags=["management"])
    async def health() -> dict:
        apps_status = []
        for state in registry.get_all():
            snap = state.metrics.get_snapshot()
            bh = bulkheads.get(state.app_id)
            apps_status.append({
                "app_id": state.app_id,
                "enabled": state.enabled,
                "current_limit": state.current_limit,
                "in_flight": bh.in_flight if bh else 0,
            })
        return {
            "status": "healthy",
            "mode": controller.mode,
            "uptime_seconds": round(time.time() - _startup_time, 1),
            "applications": apps_status,
        }

    # ── Prometheus metrics endpoint ───────────────────────────────────────────

    @app.get("/metrics", tags=["management"])
    async def metrics() -> Response:
        return PlainTextResponse(
            content=generate_latest().decode("utf-8"),
            media_type=CONTENT_TYPE_LATEST,
        )

    # ── Admin: list applications ──────────────────────────────────────────────

    @app.get("/admin/applications", tags=["admin"], dependencies=[Depends(require_admin)])
    async def admin_applications() -> dict:
        apps = []
        for state in registry.get_all():
            snap = state.metrics.get_snapshot()
            bh = bulkheads.get(state.app_id)
            apps.append({
                "app_id": state.app_id,
                "name": state.config.name,
                "base_url": state.config.base_url,
                "enabled": state.enabled,
                "current_limit": state.current_limit,
                "min_limit": state.config.min_concurrency,
                "max_limit": state.config.max_concurrency,
                "in_flight": bh.in_flight if bh else 0,
                "avg_latency_ms": round(snap.avg_latency_ms, 2),
                "p95_latency_ms": round(snap.p95_latency_ms, 2),
                "error_rate": round(snap.error_rate, 4),
                "requests_per_second": round(snap.requests_per_second, 2),
                "total_requests": snap.total_requests,
                "rejected_requests": snap.rejected_requests,
            })
        return {"applications": apps}

    # ── Admin: controller status ──────────────────────────────────────────────

    @app.get("/admin/controller/status", tags=["admin"], dependencies=[Depends(require_admin)])
    async def admin_controller_status() -> dict:
        histories = {
            app_id: controller.get_history(app_id)[-20:]
            for app_id in registry.list_ids()
        }
        return {
            "controller": controller.get_status(),
            "recent_history": histories,
        }

    # ── Admin: get mode ───────────────────────────────────────────────────────

    @app.get("/admin/controller/mode", tags=["admin"])
    async def admin_get_mode() -> dict:
        return {"mode": controller.mode}

    # ── Admin: set mode ───────────────────────────────────────────────────────

    @app.post("/admin/controller/mode", tags=["admin"], dependencies=[Depends(require_admin)])
    async def admin_set_mode(body: ModeRequest) -> dict:
        try:
            controller.set_mode(body.mode)
            CONTROLLER_MODE.set(1.0 if body.mode == "adaptive" else 0.0)
            return {"mode": controller.mode, "message": f"Mode set to '{body.mode}'."}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ── Proxy routes ──────────────────────────────────────────────────────────

    async def _proxy(request: Request) -> Response:
        """Core proxy handler used by all HTTP method routes."""
        path = request.url.path
        resolved = router.resolve(path)

        if resolved is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Not Found",
                    "detail": f"No backend application matched path '{path}'.",
                },
            )

        app_id, backend_path = resolved
        state = registry.get(app_id)
        if state is None:
            return JSONResponse(status_code=503, content={"error": "Application not available."})

        bh = bulkheads[app_id]

        async with admission.try_admit(app_id, bh, state.metrics) as admitted:
            if not admitted:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Too Many Requests",
                        "detail": (
                            f"Application '{app_id}' is at its concurrency limit "
                            f"({bh.effective_limit}). Try again later."
                        ),
                        "current_limit": bh.effective_limit,
                        "in_flight": bh.in_flight,
                    },
                )

            return await forwarder.forward(
                app_id=app_id,
                backend_url=state.config.base_url,
                backend_path=backend_path,
                request=request,
                timeout_seconds=state.config.timeout_seconds,
                metrics=state.metrics,
            )

    for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        app.add_api_route(
            "/api/{app_slug}/{path:path}",
            _proxy,
            methods=[method],
            tags=["proxy"],
        )

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("GATEWAY_PORT", "8000"))
    host = os.getenv("GATEWAY_HOST", "0.0.0.0")
    uvicorn.run(
        "gateway.main:app",
        host=host,
        port=port,
        reload=False,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
