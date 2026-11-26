"""
Tests for the AIMD adaptive controller algorithm and the AdaptiveController
background task.

Run with:
    pytest tests/test_controller.py -v
"""

from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from controller.adaptive_controller import AdaptiveController
from controller.algorithm import AdjustmentDirection, AlgorithmResult, evaluate
from gateway.config import (
    AppConfig,
    ControllerSettings,
    GatewayConfig,
    GatewaySettings,
)
from gateway.registry import ApplicationRegistry
from monitoring.collector import MetricsCollector, RequestSample
from protection.bulkhead import Bulkhead


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_samples(
    n: int,
    latency_ms: float = 50.0,
    success: bool = True,
) -> List[RequestSample]:
    """Create n identical RequestSample objects for algorithm testing."""
    now = time.monotonic()
    return [
        RequestSample(timestamp=now, latency_ms=latency_ms, success=success)
        for _ in range(n)
    ]


def _make_mixed_samples(
    total: int,
    failure_fraction: float,
    latency_ms: float = 50.0,
) -> List[RequestSample]:
    """Create samples with a mixture of success and failure."""
    now = time.monotonic()
    samples = []
    for i in range(total):
        ok = (i / total) >= failure_fraction
        samples.append(RequestSample(timestamp=now, latency_ms=latency_ms, success=ok))
    return samples


def _default_kwargs(**overrides) -> dict:
    """Return a default set of evaluate() keyword arguments."""
    defaults = dict(
        app_id="test_app",
        current_limit=10,
        min_limit=2,
        max_limit=50,
        samples=[],
        target_latency_ms=200.0,
        latency_threshold_multiplier=1.5,
        error_rate_threshold=0.1,
        additive_increase_step=1,
        multiplicative_decrease_factor=0.75,
        min_samples_required=5,
        max_increase_per_cycle=3,
        max_decrease_per_cycle=10,
        smoothing_factor=0.0,   # no smoothing by default for deterministic tests
    )
    defaults.update(overrides)
    return defaults


def _make_test_config(
    app_ids: List[str] | None = None,
    initial_concurrency: int = 10,
    eval_interval: float = 0.1,
) -> GatewayConfig:
    """Build a GatewayConfig suitable for unit tests (no file I/O needed)."""
    if app_ids is None:
        app_ids = ["app_a"]

    applications = {
        app_id: AppConfig(
            name=f"Test app {app_id}",
            base_url=f"http://localhost:900{i}",
            initial_concurrency=initial_concurrency,
            min_concurrency=2,
            max_concurrency=50,
        )
        for i, app_id in enumerate(app_ids)
    }
    return GatewayConfig(
        applications=applications,
        gateway=GatewaySettings(admin_api_key="test-key"),
        controller=ControllerSettings(
            evaluation_interval_seconds=eval_interval,
            min_samples_required=3,
            window_size_seconds=30.0,
            smoothing_factor=0.0,
        ),
    )


# ── Algorithm unit tests ──────────────────────────────────────────────────────

class TestAIMDAlgorithm:
    """Direct tests of the evaluate() function."""

    def test_insufficient_samples_holds_limit(self):
        """With fewer than min_samples_required samples the limit must not change."""
        result = evaluate(**_default_kwargs(
            samples=_make_samples(2),   # min_samples_required=5
        ))
        assert result.direction == AdjustmentDirection.UNCHANGED
        assert result.new_limit == 10
        assert "Insufficient" in result.reason

    def test_healthy_metrics_increase_limit(self):
        """Low latency and zero errors should trigger an INCREASE."""
        result = evaluate(**_default_kwargs(
            samples=_make_samples(10, latency_ms=50.0, success=True),
            # threshold = 200 * 1.5 = 300ms; 50ms is healthy
        ))
        assert result.direction == AdjustmentDirection.INCREASE
        assert result.new_limit > 10

    def test_high_latency_decreases_limit(self):
        """Latency above the threshold must trigger a DECREASE."""
        result = evaluate(**_default_kwargs(
            samples=_make_samples(10, latency_ms=400.0, success=True),
            # threshold = 200 * 1.5 = 300ms; 400ms is stressed
        ))
        assert result.direction == AdjustmentDirection.DECREASE
        assert result.new_limit < 10

    def test_high_error_rate_decreases_limit(self):
        """Error rate above error_rate_threshold must trigger a DECREASE."""
        result = evaluate(**_default_kwargs(
            samples=_make_mixed_samples(10, failure_fraction=0.5),  # 50% errors
            error_rate_threshold=0.1,
        ))
        assert result.direction == AdjustmentDirection.DECREASE

    def test_respects_min_limit(self):
        """new_limit must never go below min_limit."""
        result = evaluate(**_default_kwargs(
            current_limit=2,
            min_limit=2,
            samples=_make_samples(10, latency_ms=9999.0),   # extreme stress
        ))
        assert result.new_limit >= 2

    def test_respects_max_limit(self):
        """new_limit must never exceed max_limit."""
        result = evaluate(**_default_kwargs(
            current_limit=50,
            max_limit=50,
            samples=_make_samples(10, latency_ms=5.0),   # very healthy
        ))
        assert result.new_limit <= 50

    def test_max_increase_per_cycle_is_honoured(self):
        """Single-cycle increase must not exceed max_increase_per_cycle."""
        result = evaluate(**_default_kwargs(
            current_limit=10,
            max_limit=100,
            additive_increase_step=20,   # would like to add 20
            max_increase_per_cycle=3,    # but cap is 3
            samples=_make_samples(10, latency_ms=5.0),
        ))
        assert result.new_limit <= 10 + 3

    def test_smoothing_blends_old_and_new(self):
        """With smoothing_factor=0.5 the result should lie between old and raw new."""
        # Healthy conditions → raw increase by 1 → raw_new = 11
        # smoothed = 0.5 * 10 + 0.5 * 11 = 10.5 → rounds to 11
        result = evaluate(**_default_kwargs(
            current_limit=10,
            additive_increase_step=1,
            smoothing_factor=0.5,
            samples=_make_samples(10, latency_ms=5.0),
        ))
        # Must be between old (10) and theoretical unsmoothed new (11)
        assert 10 <= result.new_limit <= 11

    def test_result_has_correct_metrics(self):
        """AlgorithmResult must carry the sample statistics."""
        samples = _make_samples(8, latency_ms=75.0, success=True)
        result = evaluate(**_default_kwargs(samples=samples))
        assert abs(result.avg_latency_ms - 75.0) < 1.0
        assert result.error_rate == 0.0
        assert result.sample_count == 8


# ── AdaptiveController integration tests ─────────────────────────────────────

class TestAdaptiveController:
    """Tests for the AdaptiveController lifecycle and mode switching."""

    async def test_starts_and_stops_cleanly(self):
        """Controller must start and stop without errors."""
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        await ctrl.start()
        assert ctrl._running is True
        await ctrl.stop()
        assert ctrl._running is False

    async def test_mode_property(self):
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="static")
        assert ctrl.mode == "static"

    async def test_mode_switch_adaptive_to_static(self):
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        ctrl.set_mode("static")
        assert ctrl.mode == "static"

    async def test_mode_switch_static_to_adaptive(self):
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="static")
        ctrl.set_mode("adaptive")
        assert ctrl.mode == "adaptive"

    async def test_invalid_mode_raises(self):
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        with pytest.raises(ValueError):
            ctrl.set_mode("turbo")

    async def test_static_mode_does_not_change_limits(self):
        """
        In static mode the controller loop runs but must not alter any limit.
        We inject stressed metrics so that adaptive mode WOULD decrease the limit,
        then verify no change happened in static mode.
        """
        config = _make_test_config(eval_interval=0.1)
        registry = ApplicationRegistry(config)

        # Pre-load stressed metrics so a decrease would happen in adaptive mode
        state = registry.get("app_a")
        for _ in range(20):
            state.metrics.record_request(latency_ms=9999.0, success=False)

        original_limit = state.current_limit

        ctrl = AdaptiveController(registry, config.controller, mode="static")
        bh = Bulkhead("app_a", original_limit)
        registry.set_bulkhead("app_a", bh)

        await ctrl.start()
        await asyncio.sleep(0.35)   # let ≥3 evaluation cycles pass
        await ctrl.stop()

        assert state.current_limit == original_limit, (
            f"Static mode must not change the limit "
            f"(was {original_limit}, now {state.current_limit})"
        )

    async def test_adaptive_mode_decreases_limit_under_stress(self):
        """
        In adaptive mode, stressed metrics should eventually lower the limit.
        Uses a very short evaluation interval so the test completes quickly.
        """
        config = _make_test_config(initial_concurrency=20, eval_interval=0.1)
        registry = ApplicationRegistry(config)
        state = registry.get("app_a")

        # Inject stressed samples
        for _ in range(30):
            state.metrics.record_request(latency_ms=9999.0, success=False)

        bh = Bulkhead("app_a", state.current_limit)
        registry.set_bulkhead("app_a", bh)

        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        await ctrl.start()
        await asyncio.sleep(0.5)
        await ctrl.stop()

        assert state.current_limit < 20, (
            f"Expected limit to decrease from 20, but it is {state.current_limit}"
        )

    async def test_evaluates_all_registered_apps(self):
        """Controller must evaluate every registered application each cycle."""
        config = _make_test_config(app_ids=["app_a", "app_b"], eval_interval=0.1)
        registry = ApplicationRegistry(config)

        # Add healthy samples to both apps
        for app_id in ["app_a", "app_b"]:
            state = registry.get(app_id)
            bh = Bulkhead(app_id, state.current_limit)
            registry.set_bulkhead(app_id, bh)
            for _ in range(10):
                state.metrics.record_request(latency_ms=10.0, success=True)

        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        await ctrl.start()
        await asyncio.sleep(0.35)
        await ctrl.stop()

        # Both apps should have history entries
        for app_id in ["app_a", "app_b"]:
            history = ctrl.get_history(app_id)
            assert len(history) > 0, f"No history for {app_id}"

    async def test_get_status_returns_dict(self):
        config = _make_test_config()
        registry = ApplicationRegistry(config)
        ctrl = AdaptiveController(registry, config.controller, mode="adaptive")
        status = ctrl.get_status()
        assert "mode" in status
        assert "running" in status
        assert "interval_seconds" in status
