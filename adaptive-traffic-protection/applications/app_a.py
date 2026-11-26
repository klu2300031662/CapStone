"""
App A - Sample FastAPI backend for Adaptive Traffic Protection capstone project.
Runs on port 8001 (default). Provides /health, /data, and /slow endpoints.
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------
APP_A_PORT: int = int(os.getenv("APP_A_PORT", "8001"))
APP_A_DELAY_MS: int = int(os.getenv("APP_A_DELAY_MS", "50"))  # base delay in ms

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [APP-A] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("app_a")

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------
_start_time: float = time.monotonic()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="App A",
    description="Sample backend application A for the Adaptive Traffic Protection project.",
    version="1.0.0",
)

# Allow all origins for the capstone demo environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    logger.info("=" * 60)
    logger.info("  App A starting up")
    logger.info(f"  Port          : {APP_A_PORT}")
    logger.info(f"  Base delay    : {APP_A_DELAY_MS} ms")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _fake_payload(request_id: int) -> dict:
    """Build a realistic-looking fake data payload."""
    messages = [
        "Processed sensor reading",
        "Cache lookup completed",
        "Record retrieved from upstream",
        "Aggregation result ready",
        "Scheduled batch item dispatched",
    ]
    return {
        "id": request_id,
        "message": random.choice(messages),
        "value": round(random.uniform(0.0, 1000.0), 4),
        "tags": random.sample(["alpha", "beta", "gamma", "delta", "epsilon"], k=2),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", summary="Health check")
async def health() -> dict:
    """Return service health and current uptime."""
    uptime = time.monotonic() - _start_time
    logger.debug("Health check requested")
    return {
        "status": "healthy",
        "app": "app_a",
        "uptime_seconds": round(uptime, 3),
    }


@app.get("/data", summary="Fetch data with configurable base delay")
async def get_data() -> dict:
    """
    Returns a fake data payload after a configurable delay.
    The delay is set by the APP_A_DELAY_MS environment variable (default 50 ms).
    """
    delay_s = APP_A_DELAY_MS / 1000.0
    request_id = random.randint(100_000, 999_999)
    logger.info(f"[/data] request_id={request_id}  delay={APP_A_DELAY_MS}ms")
    await asyncio.sleep(delay_s)
    return {
        "app": "app_a",
        "endpoint": "data",
        "delay_ms": APP_A_DELAY_MS,
        "data": _fake_payload(request_id),
    }


@app.get("/slow", summary="Fetch data with an intentional long delay")
async def get_slow() -> dict:
    """
    Always introduces a random delay between 500 ms and 2 000 ms to simulate
    a slow upstream dependency or a heavy computation.
    """
    extra_delay_ms = random.randint(500, 2000)
    delay_s = extra_delay_ms / 1000.0
    request_id = random.randint(100_000, 999_999)
    logger.info(f"[/slow] request_id={request_id}  delay={extra_delay_ms}ms")
    await asyncio.sleep(delay_s)
    return {
        "app": "app_a",
        "endpoint": "slow",
        "delay_ms": extra_delay_ms,
        "data": _fake_payload(request_id),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "app_a:app",
        host="0.0.0.0",
        port=APP_A_PORT,
        log_level="info",
        reload=False,
    )
