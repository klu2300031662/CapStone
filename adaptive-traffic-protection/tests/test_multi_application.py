"""
Tests proving that per-application bulkhead isolation works correctly:
an overloaded Application B must not block Application A.

Run with:
    pytest tests/test_multi_application.py -v
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from gateway.config import (
    AppConfig,
    ControllerSettings,
    GatewayConfig,
    GatewaySettings,
)
from gateway.registry import ApplicationRegistry
from protection.bulkhead import Bulkhead


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_config(initial_a: int = 5, initial_b: int = 5) -> GatewayConfig:
    return GatewayConfig(
        applications={
            "app_a": AppConfig(
                name="App A",
                base_url="http://localhost:8001",
                initial_concurrency=initial_a,
                min_concurrency=1,
                max_concurrency=50,
            ),
            "app_b": AppConfig(
                name="App B",
                base_url="http://localhost:8002",
                initial_concurrency=initial_b,
                min_concurrency=1,
                max_concurrency=50,
            ),
        },
        gateway=GatewaySettings(admin_api_key="key"),
        controller=ControllerSettings(),
    )


# ── Isolation tests ───────────────────────────────────────────────────────────

class TestBulkheadIsolation:
    """Core isolation guarantee: B overload ≠ A blocked."""

    async def test_app_b_overload_does_not_block_app_a(self):
        """
        Fill app_b's bulkhead to capacity.
        Verify that app_a's bulkhead still admits new requests independently.
        """
        bh_a = Bulkhead("app_a", initial_limit=5)
        bh_b = Bulkhead("app_b", initial_limit=3)

        # Hold all 3 slots in app_b
        cms = [bh_b.acquire() for _ in range(3)]
        entered = [await cm.__aenter__() for cm in cms]
        assert all(entered), "app_b slots should all be admitted"
        assert bh_b.in_flight == 3

        # app_b is now at capacity
        async with bh_b.acquire() as b_extra:
            assert b_extra is False, "app_b should reject the extra request"

        # app_a should still admit requests freely
        async with bh_a.acquire() as a_ok:
            assert a_ok is True, "app_a must not be affected by app_b's load"

        # Clean up app_b context managers
        for cm, ok in zip(cms, entered):
            await cm.__aexit__(None, None, None)

        assert bh_b.in_flight == 0

    async def test_independent_in_flight_counts(self):
        """in_flight counters for two apps must be completely independent."""
        bh_a = Bulkhead("app_a", initial_limit=10)
        bh_b = Bulkhead("app_b", initial_limit=10)

        async with bh_a.acquire():
            async with bh_a.acquire():
                # app_a has 2 in-flight
                assert bh_a.in_flight == 2
                assert bh_b.in_flight == 0  # app_b unaffected

                async with bh_b.acquire():
                    assert bh_b.in_flight == 1
                    assert bh_a.in_flight == 2  # app_a unaffected

                assert bh_b.in_flight == 0

            assert bh_a.in_flight == 1

        assert bh_a.in_flight == 0

    async def test_independent_limits(self):
        """Changing app_b's limit must leave app_a's limit unchanged."""
        bh_a = Bulkhead("app_a", initial_limit=10)
        bh_b = Bulkhead("app_b", initial_limit=10)

        await bh_b.update_limit(3)

        assert bh_a.effective_limit == 10, "app_a limit must be unchanged"
        assert bh_b.effective_limit == 3

    async def test_registry_independent_limits(self):
        """
        Updating one application's limit via the registry must not alter
        the other application's limit.
        """
        config = _make_config(initial_a=10, initial_b=10)
        registry = ApplicationRegistry(config)

        # Update only app_b
        registry.update_limit("app_b", 4)

        state_a = registry.get("app_a")
        state_b = registry.get("app_b")

        assert state_a.current_limit == 10, "app_a must remain at 10"
        assert state_b.current_limit == 4

    async def test_concurrent_mixed_load(self):
        """
        Run 10 concurrent requests to each app simultaneously.
        Both should be admitted independently (limits are 10 each).
        """
        bh_a = Bulkhead("app_a", initial_limit=10)
        bh_b = Bulkhead("app_b", initial_limit=10)

        results_a: List[bool] = []
        results_b: List[bool] = []

        async def req_a():
            async with bh_a.acquire() as ok:
                results_a.append(ok)
                await asyncio.sleep(0.05)

        async def req_b():
            async with bh_b.acquire() as ok:
                results_b.append(ok)
                await asyncio.sleep(0.05)

        # Launch 10 from each app simultaneously
        await asyncio.gather(
            *[req_a() for _ in range(10)],
            *[req_b() for _ in range(10)],
        )

        assert all(results_a), f"All app_a requests should be admitted: {results_a}"
        assert all(results_b), f"All app_b requests should be admitted: {results_b}"

    async def test_app_b_at_limit_app_a_still_serves(self):
        """
        Hold app_b at exactly its limit while sending requests to app_a.
        All app_a requests should succeed.
        """
        LIMIT = 4
        bh_a = Bulkhead("app_a", initial_limit=LIMIT)
        bh_b = Bulkhead("app_b", initial_limit=LIMIT)

        held_cms = [bh_b.acquire() for _ in range(LIMIT)]
        held_flags = [await cm.__aenter__() for cm in held_cms]
        assert all(held_flags)
        assert bh_b.in_flight == LIMIT

        # Now send requests to app_a — should all succeed
        a_results: List[bool] = []
        for _ in range(5):
            async with bh_a.acquire() as ok:
                a_results.append(ok)

        assert all(a_results), "app_a must admit requests while app_b is at limit"

        # Release app_b
        for cm in held_cms:
            await cm.__aexit__(None, None, None)

    async def test_different_limits_per_app(self):
        """Each app can have a different independent concurrency limit."""
        bh_a = Bulkhead("app_a", initial_limit=15)
        bh_b = Bulkhead("app_b", initial_limit=3)

        assert bh_a.effective_limit == 15
        assert bh_b.effective_limit == 3

        # Fill app_b to capacity
        cms = [bh_b.acquire() for _ in range(3)]
        flags = [await cm.__aenter__() for cm in cms]
        assert all(flags)

        # app_a with limit 15 should still admit many requests
        a_admitted = 0
        a_cms = [bh_a.acquire() for _ in range(10)]
        for cm in a_cms:
            ok = await cm.__aenter__()
            if ok:
                a_admitted += 1

        assert a_admitted == 10, f"Expected 10 from app_a, got {a_admitted}"

        # Cleanup
        for cm in cms + a_cms:
            await cm.__aexit__(None, None, None)

    async def test_registry_lists_both_apps(self):
        config = _make_config()
        registry = ApplicationRegistry(config)
        ids = set(registry.list_ids())
        assert "app_a" in ids
        assert "app_b" in ids

    async def test_registry_get_returns_correct_state(self):
        config = _make_config(initial_a=7, initial_b=12)
        registry = ApplicationRegistry(config)

        state_a = registry.get("app_a")
        state_b = registry.get("app_b")

        assert state_a.current_limit == 7
        assert state_b.current_limit == 12

    async def test_registry_unknown_app_returns_none(self):
        config = _make_config()
        registry = ApplicationRegistry(config)
        assert registry.get("nonexistent") is None
