"""
Request router.

Maps an incoming gateway URL path to a registered application ID and the
remaining path that should be forwarded to the backend.

Example:
    /api/app-a/data  →  app_id="app_a",  backend_path="/data"
    /api/app-b/slow  →  app_id="app_b",  backend_path="/slow"

The route prefix is built from the application ID: hyphens in the app_id
are preserved in the URL segment (app_a → app-a).
"""

from __future__ import annotations

from typing import Optional, Tuple

from gateway.registry import ApplicationRegistry
from monitoring.logger import get_logger

logger = get_logger(__name__)

# URL prefix that all gateway-routed requests must start with
GATEWAY_PREFIX = "/api"


def _app_id_to_slug(app_id: str) -> str:
    """Convert an app_id ('app_a') to its URL slug ('app-a')."""
    return app_id.replace("_", "-")


def _slug_to_app_id(slug: str) -> str:
    """Convert a URL slug ('app-a') back to an app_id ('app_a')."""
    return slug.replace("-", "_")


class RequestRouter:
    """
    Maps incoming request paths to registered backend applications.

    The routing table is rebuilt each time the registry is passed in,
    so adding an application to the registry makes it immediately routable.
    """

    def __init__(self, registry: ApplicationRegistry) -> None:
        self._registry = registry

    def resolve(self, path: str) -> Optional[Tuple[str, str]]:
        """
        Resolve a gateway path to (app_id, backend_path).

        Returns None if the path does not match any registered application.

        Parameters
        ----------
        path:
            The raw URL path from the incoming HTTP request (e.g. "/api/app-a/data").

        Returns
        -------
        (app_id, backend_path) or None
            app_id      – the registry key for the matched application
            backend_path – the remaining path to forward to the backend
        """
        if not path.startswith(GATEWAY_PREFIX + "/"):
            return None

        # Strip the /api prefix
        remainder = path[len(GATEWAY_PREFIX):]  # e.g. "/app-a/data"

        parts = remainder.lstrip("/").split("/", 1)
        if not parts or not parts[0]:
            return None

        slug = parts[0]  # e.g. "app-a"
        backend_path = "/" + parts[1] if len(parts) > 1 else "/"

        app_id = _slug_to_app_id(slug)

        if not self._registry.is_registered(app_id):
            logger.debug("No application registered for slug '%s' (app_id='%s')", slug, app_id)
            return None

        state = self._registry.get(app_id)
        if state is None or not state.enabled:
            logger.debug("Application '%s' is disabled", app_id)
            return None

        return app_id, backend_path
