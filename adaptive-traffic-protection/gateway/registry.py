"""
Application registry.

Maintains live state for every registered backend application:
  - Configuration (from YAML)
  - Current concurrency limit
  - MetricsCollector instance
  - Reference to the per-application Bulkhead

The registry is the single source of truth for application state.
All gateway components obtain application state through the registry.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from gateway.config import AppConfig, GatewayConfig
from monitoring.collector import MetricsCollector
from monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AppState:
    """
    Mutable runtime state for one registered backend application.

    This is NOT the Pydantic config model — it is the live object
    that changes as the adaptive controller runs.
    """
    app_id: str
    config: AppConfig
    current_limit: int
    metrics: MetricsCollector
    # The Bulkhead is set after construction (to avoid circular imports)
    bulkhead: Optional[object] = field(default=None, repr=False)
    enabled: bool = True


class ApplicationRegistry:
    """
    Thread-safe registry of all registered backend applications.

    Instantiated once at gateway startup. Applications are loaded from
    configuration and cannot be added at runtime in this prototype
    (a future enhancement would add a hot-reload endpoint).
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._lock = threading.RLock()
        self._apps: Dict[str, AppState] = {}
        self._controller_config = config.controller
        self._load_applications(config)

    def _load_applications(self, config: GatewayConfig) -> None:
        """Populate the registry from validated configuration."""
        for app_id, app_config in config.applications.items():
            if not app_config.enabled:
                logger.info("Skipping disabled application: %s", app_id)
                continue

            state = AppState(
                app_id=app_id,
                config=app_config,
                current_limit=app_config.initial_concurrency,
                metrics=MetricsCollector(
                    app_id=app_id,
                    window_seconds=self._controller_config.window_size_seconds,
                ),
            )
            self._apps[app_id] = state
            logger.info(
                "Registered application '%s' -> %s (limit=%d)",
                app_id,
                app_config.base_url,
                app_config.initial_concurrency,
            )

    # ── Read access ───────────────────────────────────────────────────────────

    def get(self, app_id: str) -> Optional[AppState]:
        """Return the AppState for the given app_id, or None."""
        with self._lock:
            return self._apps.get(app_id)

    def get_all(self) -> List[AppState]:
        """Return a list of all registered AppState objects."""
        with self._lock:
            return list(self._apps.values())

    def list_ids(self) -> List[str]:
        """Return a list of all registered application IDs."""
        with self._lock:
            return list(self._apps.keys())

    def is_registered(self, app_id: str) -> bool:
        with self._lock:
            return app_id in self._apps

    # ── Limit management ──────────────────────────────────────────────────────

    def update_limit(self, app_id: str, new_limit: int) -> bool:
        """
        Safely update the concurrency limit for an application.

        The new limit is clamped to [min_concurrency, max_concurrency].
        Returns True if the app was found and updated.
        """
        with self._lock:
            state = self._apps.get(app_id)
            if state is None:
                return False

            clamped = max(
                state.config.min_concurrency,
                min(state.config.max_concurrency, new_limit),
            )
            state.current_limit = clamped
            state.metrics.set_concurrency_limit(clamped)
            return True

    def set_bulkhead(self, app_id: str, bulkhead: object) -> None:
        """Associate a Bulkhead instance with an application."""
        with self._lock:
            state = self._apps.get(app_id)
            if state:
                state.bulkhead = bulkhead
