"""
Adaptive concurrency controller.

Runs as a background asyncio task. On each evaluation cycle it:

  1. Reads the recent request samples for every registered application.
  2. Calls the AIMD algorithm to compute a new concurrency limit.
  3. Updates the application registry and its associated Bulkhead.
  4. Records the change in Prometheus metrics.

The controller supports two operating modes:
  - adaptive  : limits are updated according to observed metrics.
  - static    : limits are frozen at their current values (no changes).

Switching between modes is safe at any time and does not interrupt
in-flight requests.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, List

from controller.algorithm import AdjustmentDirection, AlgorithmResult, evaluate
from gateway.config import ControllerSettings
from gateway.registry import ApplicationRegistry
from monitoring.logger import get_logger
from monitoring.metrics import (
    CONCURRENCY_LIMIT,
    CONTROLLER_MODE,
    CONTROLLER_UPDATES,
)

logger = get_logger(__name__)


class AdaptiveController:
    """
    Background controller that adjusts per-application concurrency limits.

    Usage::

        controller = AdaptiveController(registry, settings, mode="adaptive")
        await controller.start()   # starts the background loop
        ...
        await controller.stop()    # stops the loop gracefully
    """

    def __init__(
        self,
        registry: ApplicationRegistry,
        settings: ControllerSettings,
        mode: str = "adaptive",
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._mode = mode  # "adaptive" | "static"
        self._task: asyncio.Task | None = None
        self._running = False

        # History of limit changes per app (for dashboard)
        self._history: Dict[str, List[dict]] = {
            app.app_id: [] for app in registry.get_all()
        }

        # Update Prometheus mode gauge
        CONTROLLER_MODE.set(1.0 if mode == "adaptive" else 0.0)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background evaluation loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="adaptive-controller")
        logger.info("Adaptive controller started in %s mode.", self._mode)

    async def stop(self) -> None:
        """Stop the background evaluation loop gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Adaptive controller stopped.")

    # ── Mode management ───────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, new_mode: str) -> None:
        """
        Switch operating mode.

        Parameters
        ----------
        new_mode: "adaptive" | "static"
        """
        if new_mode not in ("adaptive", "static"):
            raise ValueError(f"Invalid mode '{new_mode}'. Must be 'adaptive' or 'static'.")
        old = self._mode
        self._mode = new_mode
        CONTROLLER_MODE.set(1.0 if new_mode == "adaptive" else 0.0)
        logger.info("Controller mode changed: %s → %s", old, new_mode)

    # ── Evaluation loop ───────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Periodic evaluation loop."""
        interval = self._settings.evaluation_interval_seconds
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._mode == "adaptive":
                    await self._evaluate_all()
                else:
                    logger.debug("Controller in static mode — skipping evaluation.")
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in controller loop: %s", exc)

    async def _evaluate_all(self) -> None:
        """Evaluate every registered application and update limits if needed."""
        for state in self._registry.get_all():
            try:
                await self._evaluate_one(state.app_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Error evaluating application '%s': %s", state.app_id, exc
                )

    async def _evaluate_one(self, app_id: str) -> None:
        """Run the AIMD algorithm for a single application."""
        state = self._registry.get(app_id)
        if state is None:
            return

        samples = state.metrics.get_window_samples()
        s = self._settings

        result: AlgorithmResult = evaluate(
            app_id=app_id,
            current_limit=state.current_limit,
            min_limit=state.config.min_concurrency,
            max_limit=state.config.max_concurrency,
            samples=samples,
            target_latency_ms=s.target_latency_ms,
            latency_threshold_multiplier=s.latency_threshold_multiplier,
            error_rate_threshold=s.error_rate_threshold,
            additive_increase_step=s.additive_increase_step,
            multiplicative_decrease_factor=s.multiplicative_decrease_factor,
            min_samples_required=s.min_samples_required,
            max_increase_per_cycle=s.max_increase_per_cycle,
            max_decrease_per_cycle=s.max_decrease_per_cycle,
            smoothing_factor=s.smoothing_factor,
        )

        logger.info(
            "[%s] limit %d→%d | %s | avg=%.1fms p95=%.1fms err=%.2f%% samples=%d",
            app_id,
            state.current_limit,
            result.new_limit,
            result.direction.value,
            result.avg_latency_ms,
            result.p95_latency_ms,
            result.error_rate * 100,
            result.sample_count,
        )

        # Update the registry
        self._registry.update_limit(app_id, result.new_limit)

        # Update the bulkhead
        if state.bulkhead is not None:
            await state.bulkhead.update_limit(result.new_limit)

        # Update Prometheus
        CONCURRENCY_LIMIT.labels(app_id=app_id).set(result.new_limit)
        CONTROLLER_UPDATES.labels(
            app_id=app_id, direction=result.direction.value
        ).inc()

        # Store history for dashboard
        self._history[app_id].append({
            "timestamp": time.time(),
            "limit": result.new_limit,
            "direction": result.direction.value,
            "avg_latency_ms": result.avg_latency_ms,
            "p95_latency_ms": result.p95_latency_ms,
            "error_rate": result.error_rate,
        })
        # Keep only last 500 entries per app
        if len(self._history[app_id]) > 500:
            self._history[app_id] = self._history[app_id][-500:]

    # ── Introspection ─────────────────────────────────────────────────────────

    def get_history(self, app_id: str) -> List[dict]:
        """Return the recorded limit-change history for an application."""
        return list(self._history.get(app_id, []))

    def get_status(self) -> dict:
        """Return a status dict summarising the controller state."""
        return {
            "mode": self._mode,
            "running": self._running,
            "interval_seconds": self._settings.evaluation_interval_seconds,
            "target_latency_ms": self._settings.target_latency_ms,
            "error_rate_threshold": self._settings.error_rate_threshold,
            "additive_increase_step": self._settings.additive_increase_step,
            "multiplicative_decrease_factor": self._settings.multiplicative_decrease_factor,
        }
