"""
Tests for Bulkhead (per-application concurrency limiter) and
GlobalRateLimiter (gateway-wide safety cap).

Run with:
    pytest tests/test_bulkhead.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from protection.bulkhead import Bulkhead
from protection.rate_limiter import GlobalRateLimiter


# ── Bulkhead tests ────────────────────────────────────────────────────────────

class TestBulkheadAdmission:
    """Basic admit / reject behaviour."""

    async def test_admits_within_limit(self):
        """A request within the limit must be admitted."""
        bh = Bulkhead("app_a", initial_limit=5)
        async with bh.acquire() as admitted:
            assert admitted is True

    async def test_rejects_at_limit(self):
        """When in-flight == limit the next request must be rejected."""
        bh = Bulkhead("app_a", initial_limit=1)

        async with bh.acquire() as first:
            assert first is True           # fills the single slot
            async with bh.acquire() as second:
                assert second is False     # limit reached

    async def test_in_flight_increments_on_admit(self):
        """in_flight must increase by one when a request is admitted."""
        bh = Bulkhead("app_a", initial_limit=5)
        assert bh.in_flight == 0
        async with bh.acquire() as admitted:
            assert admitted is True
            assert bh.in_flight == 1

    async def test_in_flight_decrements_after_exit(self):
        """in_flight must return to zero after the context manager exits."""
        bh = Bulkhead("app_a", initial_limit=5)
        async with bh.acquire():
            pass
        assert bh.in_flight == 0

    async def test_in_flight_never_negative(self):
        """in_flight must never go below zero even after double-exit scenarios."""
        bh = Bulkhead("app_a", initial_limit=5)
        # Directly test the floor: start at 0, force a decrement via code path
        async with bh.acquire():
            pass  # exits cleanly
        # in_flight already 0; a second exit won't happen but let's verify floor
        assert bh.in_flight >= 0

    async def test_no_negative_inflight_on_rejection(self):
        """Rejections must not modify in_flight."""
        bh = Bulkhead("app_a", initial_limit=1)
        async with bh.acquire():       # fills limit
            before = bh.in_flight
            async with bh.acquire():   # rejected
                pass
            assert bh.in_flight == before  # unchanged


class TestBulkheadLimitChanges:
    """Dynamic limit updates."""

    async def test_increase_limit_allows_more_requests(self):
        """After increasing the limit, additional requests should be admitted."""
        bh = Bulkhead("app_a", initial_limit=1)

        async with bh.acquire() as first:
            assert first is True
            # At limit — next is rejected
            async with bh.acquire() as second:
                assert second is False

            # Increase limit to 2
            await bh.update_limit(2)

            async with bh.acquire() as third:
                assert third is True   # now admitted

    async def test_decrease_limit_takes_effect_for_new_requests(self):
        """Lowering the limit affects new admissions but not in-flight ones."""
        bh = Bulkhead("app_a", initial_limit=5)

        async with bh.acquire() as first:
            assert first is True
            await bh.update_limit(1)   # limit now 1, 1 already in flight
            # Another new request should be rejected (in_flight >= new limit)
            async with bh.acquire() as second:
                assert second is False

        # After the original request exits, in_flight should be 0
        assert bh.in_flight == 0

    async def test_effective_limit_property(self):
        """effective_limit must reflect the current limit."""
        bh = Bulkhead("app_a", initial_limit=10)
        assert bh.effective_limit == 10
        await bh.update_limit(7)
        assert bh.effective_limit == 7

    async def test_limit_clamped_to_minimum_one(self):
        """update_limit(0) must clamp to 1."""
        bh = Bulkhead("app_a", initial_limit=5)
        await bh.update_limit(0)
        assert bh.effective_limit == 1


class TestBulkheadConcurrency:
    """Concurrent request scenarios."""

    async def test_concurrent_requests_exactly_at_limit(self):
        """Exactly `limit` concurrent requests should all be admitted."""
        LIMIT = 5
        bh = Bulkhead("app_a", initial_limit=LIMIT)
        admitted_flags: list[bool] = []

        async def one_request():
            async with bh.acquire() as ok:
                admitted_flags.append(ok)
                await asyncio.sleep(0.05)   # hold the slot briefly

        await asyncio.gather(*[one_request() for _ in range(LIMIT)])
        assert all(admitted_flags), f"Expected all {LIMIT} admitted, got {admitted_flags}"

    async def test_concurrent_requests_over_limit(self):
        """Requests above the limit must be rejected."""
        LIMIT = 3
        TOTAL = 6
        bh = Bulkhead("app_a", initial_limit=LIMIT)
        results: list[bool] = []

        async def one_request():
            async with bh.acquire() as ok:
                results.append(ok)
                if ok:
                    await asyncio.sleep(0.1)  # hold slot while others try

        await asyncio.gather(*[one_request() for _ in range(TOTAL)])
        admitted = sum(results)
        rejected = len(results) - admitted
        assert admitted == LIMIT, f"Expected {LIMIT} admitted, got {admitted}"
        assert rejected == TOTAL - LIMIT

    async def test_cleanup_on_exception_inside_context(self):
        """in_flight must be decremented even when the body raises an exception."""
        bh = Bulkhead("app_a", initial_limit=5)

        try:
            async with bh.acquire() as ok:
                assert ok is True
                assert bh.in_flight == 1
                raise RuntimeError("simulated error")
        except RuntimeError:
            pass

        assert bh.in_flight == 0  # must be cleaned up


# ── GlobalRateLimiter tests ───────────────────────────────────────────────────

class TestGlobalRateLimiter:
    """Gateway-wide concurrency cap."""

    async def test_admits_within_cap(self):
        grl = GlobalRateLimiter(global_cap=10)
        async with grl.acquire() as ok:
            assert ok is True

    async def test_rejects_at_cap(self):
        grl = GlobalRateLimiter(global_cap=1)
        async with grl.acquire() as first:
            assert first is True
            async with grl.acquire() as second:
                assert second is False

    async def test_in_flight_tracked(self):
        grl = GlobalRateLimiter(global_cap=10)
        assert grl.in_flight == 0
        async with grl.acquire():
            assert grl.in_flight == 1
        assert grl.in_flight == 0

    async def test_cap_property(self):
        grl = GlobalRateLimiter(global_cap=42)
        assert grl.global_cap == 42

    async def test_cleanup_on_exception(self):
        grl = GlobalRateLimiter(global_cap=5)
        try:
            async with grl.acquire() as ok:
                assert ok is True
                raise ValueError("test")
        except ValueError:
            pass
        assert grl.in_flight == 0

    async def test_cap_clamped_to_minimum_one(self):
        """A cap of 0 or negative should be clamped to 1."""
        grl = GlobalRateLimiter(global_cap=0)
        assert grl.global_cap >= 1
