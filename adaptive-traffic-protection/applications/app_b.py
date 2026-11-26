"""
App B - Configurable FastAPI backend for Adaptive Traffic Protection capstone project.
Runs on port 8002 (default).

Provides:
  GET  /health            – current operational state
  GET  /data              – data endpoint with configurable delay & failure injection
  GET  /slow              – same as /data but adds an extra slowdown on top
  POST /admin/delay       – set base response delay at runtime (requires X-Admin-Key)
  POST /admin/failure     – enable/disable failure injection and set rate (requires X-Admin-Key)
"""

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------
APP_B_PORT: int = int(os.getenv("APP_B_PORT", "8002"))
APP_B_ADMIN_KEY: str = os.getenv("APP_B_ADMIN_KEY", "app-b-admin-key")

# Initial tunable parameters
_INITIAL_DELAY_MS: int = int(os.getenv("APP_B_DELAY_MS", "80"))
_INITIAL_FAILURE_RATE: float = float(os.getenv("APP_B_FAILURE_RATE", "0.0"))

# Extra delay added by /slow on top of the base delay (ms)
_SLOW_EXTRA_MS: int = int(os.getenv("APP_B_SLOW_EXTRA_MS", "800"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [APP-B] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("app_b")


# ---------------------------------------------------------------------------
# Mutable runtime state (module-level, no database)
# ---------------------------------------------------------------------------
@dataclass
class AppBState:
    delay_ms: int = _INITIAL_DELAY_MS
    failure_rate: float = _INITIAL_FAILURE_RATE
    failure_enabled: bool = False
    mode: str = "normal"  # "normal" | "degraded"

    def update_mode(self) -> None:
        """Recompute the human-readable mode label."""
        if self.failure_enabled and self.failure_rate > 0.0:
            self.mode = "degraded"
        else:
            self.mode = "normal"


state = AppBState()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="App B",
    description=(
        "Configurable backend application B for the Adaptive Traffic Protection project. "
        "Supports runtime delay and failure-injection tuning via admin endpoints."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_start_time: float = time.monotonic()


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    logger.info("=" * 60)
    logger.info("  App B starting up")
    logger.info(f"  Port          : {APP_B_PORT}")
    logger.info(f"  Base delay    : {state.delay_ms} ms")
    logger.info(f"  Failure rate  : {state.failure_rate:.0%}")
    logger.info(f"  Admin key     : {'*' * len(APP_B_ADMIN_KEY)}")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class DelayRequest(BaseModel):
    delay_ms: int = Field(..., ge=0, le=30_000, description="Base delay in milliseconds (0–30 000)")


class FailureRequest(BaseModel):
    failure_rate: float = Field(..., ge=0.0, le=1.0, description="Probability of failure per request (0.0–1.0)")
    enabled: bool = Field(..., description="Whether failure injection is active")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _require_admin(x_admin_key: Optional[str]) -> None:
    """Raise 403 if the provided admin key does not match the configured secret."""
    if x_admin_key != APP_B_ADMIN_KEY:
        logger.warning("Admin auth failure – bad or missing X-Admin-Key")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Admin-Key header.",
        )


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def _fake_payload(request_id: int) -> dict:
    """Build a realistic-looking fake data payload."""
    categories = ["telemetry", "inventory", "user-event", "audit", "metric"]
    return {
        "id": request_id,
        "category": random.choice(categories),
        "value": round(random.gauss(50.0, 15.0), 4),
        "confidence": round(random.uniform(0.70, 0.99), 4),
        "region": random.choice(["us-east", "eu-west", "ap-south"]),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def _maybe_fail() -> None:
    """
    If failure injection is enabled and the random draw is below the configured rate,
    raise an HTTP 500 to simulate an internal error.
    """
    if state.failure_enabled and random.random() < state.failure_rate:
        logger.warning(
            f"Simulated failure triggered (rate={state.failure_rate:.0%})"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Simulated failure", "app": "app_b"},
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", summary="Health and current configuration state")
async def health() -> dict:
    """Return operational health, current delay, and failure-injection settings."""
    uptime = time.monotonic() - _start_time
    return {
        "status": "healthy" if state.mode == "normal" else "degraded",
        "app": "app_b",
        "mode": state.mode,
        "delay_ms": state.delay_ms,
        "failure_rate": state.failure_rate,
        "failure_enabled": state.failure_enabled,
        "uptime_seconds": round(uptime, 3),
    }


@app.get("/data", summary="Fetch data with configurable delay and optional failure injection")
async def get_data() -> dict:
    """
    Returns a fake data payload.
    - Sleeps for `state.delay_ms` milliseconds before responding.
    - May raise HTTP 500 according to the configured failure rate.
    """
    request_id = random.randint(100_000, 999_999)
    logger.info(
        f"[/data] request_id={request_id}  delay={state.delay_ms}ms  "
        f"failure_enabled={state.failure_enabled}  failure_rate={state.failure_rate:.0%}"
    )
    _maybe_fail()
    await asyncio.sleep(state.delay_ms / 1000.0)
    return {
        "app": "app_b",
        "endpoint": "data",
        "delay_ms": state.delay_ms,
        "data": _fake_payload(request_id),
    }


@app.get("/slow", summary="Fetch data with extra intentional latency")
async def get_slow() -> dict:
    """
    Returns a fake data payload after the configured base delay PLUS an extra
    slowdown (default 800 ms, configurable via APP_B_SLOW_EXTRA_MS env var).
    Failure injection still applies.
    """
    total_delay_ms = state.delay_ms + _SLOW_EXTRA_MS
    request_id = random.randint(100_000, 999_999)
    logger.info(
        f"[/slow] request_id={request_id}  total_delay={total_delay_ms}ms  "
        f"(base={state.delay_ms}ms + extra={_SLOW_EXTRA_MS}ms)"
    )
    _maybe_fail()
    await asyncio.sleep(total_delay_ms / 1000.0)
    return {
        "app": "app_b",
        "endpoint": "slow",
        "delay_ms": total_delay_ms,
        "base_delay_ms": state.delay_ms,
        "extra_delay_ms": _SLOW_EXTRA_MS,
        "data": _fake_payload(request_id),
    }


@app.post(
    "/admin/delay",
    summary="Set the base response delay at runtime",
    status_code=status.HTTP_200_OK,
)
async def admin_set_delay(
    body: DelayRequest,
    x_admin_key: Optional[str] = Header(default=None),
) -> dict:
    """
    Update the base response delay (in ms) for /data and /slow.
    Requires the `X-Admin-Key` header to equal the configured admin secret.
    """
    _require_admin(x_admin_key)
    old_delay = state.delay_ms
    state.delay_ms = body.delay_ms
    logger.info(f"[admin] delay updated: {old_delay}ms → {state.delay_ms}ms")
    return {
        "success": True,
        "app": "app_b",
        "previous_delay_ms": old_delay,
        "new_delay_ms": state.delay_ms,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post(
    "/admin/failure",
    summary="Configure failure injection",
    status_code=status.HTTP_200_OK,
)
async def admin_set_failure(
    body: FailureRequest,
    x_admin_key: Optional[str] = Header(default=None),
) -> dict:
    """
    Enable or disable failure injection and set the per-request failure probability.
    Requires the `X-Admin-Key` header.

    - `failure_rate`: float between 0.0 and 1.0 (probability of HTTP 500 per request).
    - `enabled`: whether the injection is active.
    """
    _require_admin(x_admin_key)
    old_rate = state.failure_rate
    old_enabled = state.failure_enabled

    state.failure_rate = body.failure_rate
    state.failure_enabled = body.enabled
    state.update_mode()

    logger.info(
        f"[admin] failure injection updated: "
        f"enabled {old_enabled}→{state.failure_enabled}, "
        f"rate {old_rate:.0%}→{state.failure_rate:.0%}, "
        f"mode={state.mode}"
    )
    return {
        "success": True,
        "app": "app_b",
        "failure_enabled": state.failure_enabled,
        "failure_rate": state.failure_rate,
        "mode": state.mode,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "app_b:app",
        host="0.0.0.0",
        port=APP_B_PORT,
        log_level="info",
        reload=False,
    )
