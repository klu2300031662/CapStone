"""
AIMD-style adaptive concurrency algorithm.

Background
----------
AIMD stands for Additive Increase / Multiplicative Decrease. It was originally
designed for TCP congestion control and has been adapted here for controlling
the number of concurrent requests allowed into a backend application.

How it works
------------
The algorithm maintains a concurrency limit for each backend application. On
every evaluation cycle (default: every 5 seconds) it looks at the recent
performance metrics for the application and decides whether to:

  1. INCREASE the limit (backend has spare capacity):
       new_limit = old_limit + additive_step  (clamped at max_increase_per_cycle)

  2. DECREASE the limit (backend is under stress):
       new_limit = old_limit * decrease_factor  (multiplicative, e.g. ×0.75)

  3. HOLD the limit (insufficient data or borderline conditions):
       new_limit = old_limit

The decrease is multiplicative so it reacts quickly to overload. The increase
is additive so recovery is cautious and does not overshoot.

Smoothing / hysteresis
----------------------
A smoothing factor blends the raw computed limit with the previous limit to
avoid rapid oscillations:

    smoothed = previous * smoothing_factor + raw_new * (1 - smoothing_factor)

The smoothed result is then clamped to [min_concurrency, max_concurrency].

Limitations
-----------
- The algorithm does not model queue depth or service-time distributions.
- It reacts to aggregate latency within a 30-second window, so a sudden
  burst of slow requests may take one or two cycles to suppress.
- The target_latency_ms must be tuned for the specific backend.
- It does NOT guarantee zero dropped requests during overload — it only
  minimises unnecessary dropping during normal conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from monitoring.collector import RequestSample


class AdjustmentDirection(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    UNCHANGED = "unchanged"


@dataclass
class AlgorithmResult:
    """The outcome of one algorithm evaluation cycle."""
    new_limit: int
    direction: AdjustmentDirection
    reason: str
    avg_latency_ms: float
    p95_latency_ms: float
    error_rate: float
    sample_count: int


def evaluate(
    *,
    app_id: str,
    current_limit: int,
    min_limit: int,
    max_limit: int,
    samples: List[RequestSample],
    target_latency_ms: float,
    latency_threshold_multiplier: float,
    error_rate_threshold: float,
    additive_increase_step: int,
    multiplicative_decrease_factor: float,
    min_samples_required: int,
    max_increase_per_cycle: int,
    max_decrease_per_cycle: int,
    smoothing_factor: float,
) -> AlgorithmResult:
    """
    Evaluate one AIMD cycle and return a new concurrency limit.

    Parameters
    ----------
    app_id:
        Identifier of the application being evaluated (for logging).
    current_limit:
        The concurrency limit that was in effect during the observation window.
    min_limit / max_limit:
        Hard bounds on the returned limit.
    samples:
        Request samples from the rolling observation window.
    target_latency_ms:
        The ideal maximum response latency in milliseconds.
    latency_threshold_multiplier:
        The limit is decreased when avg_latency > target * multiplier.
    error_rate_threshold:
        The limit is decreased when error_rate > this value (0.0 – 1.0).
    additive_increase_step:
        How many permits to add on each healthy cycle (before smoothing).
    multiplicative_decrease_factor:
        Factor applied to the limit on each stressed cycle (e.g. 0.75).
    min_samples_required:
        Minimum number of samples in the window before making any change.
    max_increase_per_cycle / max_decrease_per_cycle:
        Hard cap on how much the limit can change in one evaluation cycle.
    smoothing_factor:
        Weight given to the previous limit in the blended result (0–1).
        Higher values = more inertia = slower changes.

    Returns
    -------
    AlgorithmResult
    """
    n = len(samples)

    # ── Compute latency statistics ────────────────────────────────────────────
    latencies = [s.latency_ms for s in samples]
    avg_latency = _mean(latencies)
    p95_latency = _percentile(latencies, 95)

    # ── Compute error rate ────────────────────────────────────────────────────
    failed_count = sum(1 for s in samples if not s.success)
    error_rate = failed_count / n if n > 0 else 0.0

    # ── Guard: insufficient data ──────────────────────────────────────────────
    if n < min_samples_required:
        return AlgorithmResult(
            new_limit=current_limit,
            direction=AdjustmentDirection.UNCHANGED,
            reason=f"Insufficient samples ({n} < {min_samples_required}). Holding limit.",
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
            error_rate=error_rate,
            sample_count=n,
        )

    threshold_latency = target_latency_ms * latency_threshold_multiplier

    # ── Decide direction ──────────────────────────────────────────────────────
    stressed = avg_latency > threshold_latency or error_rate > error_rate_threshold

    if stressed:
        # Multiplicative decrease
        raw_new = current_limit * multiplicative_decrease_factor
        # Clamp maximum decrease per cycle
        decrease_cap = current_limit - max_decrease_per_cycle
        raw_new = max(raw_new, decrease_cap)
        direction = AdjustmentDirection.DECREASE
        reason = (
            f"Latency {avg_latency:.1f}ms > threshold {threshold_latency:.1f}ms "
            f"or error_rate {error_rate:.2%} > {error_rate_threshold:.2%}. Decreasing."
        )
    else:
        # Additive increase (capped)
        increase = min(additive_increase_step, max_increase_per_cycle)
        raw_new = current_limit + increase
        direction = AdjustmentDirection.INCREASE
        reason = (
            f"Latency {avg_latency:.1f}ms ≤ threshold {threshold_latency:.1f}ms "
            f"and error_rate {error_rate:.2%} ≤ {error_rate_threshold:.2%}. Increasing."
        )

    # ── Smoothing (exponential blending) ─────────────────────────────────────
    # smoothed = alpha * old + (1 - alpha) * new
    # A higher alpha means more inertia — the limit changes more slowly.
    smoothed = smoothing_factor * current_limit + (1 - smoothing_factor) * raw_new

    # ── Clamp to configured bounds ────────────────────────────────────────────
    new_limit = int(round(smoothed))
    new_limit = max(min_limit, min(max_limit, new_limit))

    # Determine effective direction after smoothing/clamping
    if new_limit > current_limit:
        direction = AdjustmentDirection.INCREASE
    elif new_limit < current_limit:
        direction = AdjustmentDirection.DECREASE
    else:
        direction = AdjustmentDirection.UNCHANGED

    return AlgorithmResult(
        new_limit=new_limit,
        direction=direction,
        reason=reason,
        avg_latency_ms=avg_latency,
        p95_latency_ms=p95_latency,
        error_rate=error_rate,
        sample_count=n,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(values: List[float], pct: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    index = int(len(sorted_vals) * pct / 100)
    index = min(index, len(sorted_vals) - 1)
    return sorted_vals[index]
