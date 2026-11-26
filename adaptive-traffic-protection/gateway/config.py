"""
Gateway configuration loader.

Reads applications.yaml and environment variables to produce a fully
validated GatewayConfig object at startup.
"""

import os
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class AppConfig(BaseModel):
    """Configuration for a single registered backend application."""
    name: str
    base_url: str
    docker_base_url: Optional[str] = None
    initial_concurrency: int = Field(ge=1)
    min_concurrency: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    enabled: bool = True
    timeout_seconds: float = Field(gt=0.0, default=10.0)
    description: str = ""

    @field_validator("max_concurrency")
    @classmethod
    def max_gte_min(cls, v: int, info) -> int:
        if "min_concurrency" in info.data and v < info.data["min_concurrency"]:
            raise ValueError("max_concurrency must be >= min_concurrency")
        return v


class GatewaySettings(BaseModel):
    """Top-level gateway settings."""
    host: str = "0.0.0.0"
    port: int = 8000
    global_concurrency_cap: int = 200
    admin_api_key: str = "change-me"
    mode: str = "adaptive"  # "adaptive" or "static"

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("adaptive", "static"):
            raise ValueError("mode must be 'adaptive' or 'static'")
        return v


class ControllerSettings(BaseModel):
    """Adaptive controller tuning parameters."""
    evaluation_interval_seconds: float = 5.0
    target_latency_ms: float = 200.0
    latency_threshold_multiplier: float = 1.5
    error_rate_threshold: float = 0.1
    additive_increase_step: int = 1
    multiplicative_decrease_factor: float = 0.75
    min_samples_required: int = 5
    window_size_seconds: float = 30.0
    max_increase_per_cycle: int = 3
    max_decrease_per_cycle: int = 10
    smoothing_factor: float = 0.3


class GatewayConfig(BaseModel):
    """Complete validated gateway configuration."""
    applications: Dict[str, AppConfig]
    gateway: GatewaySettings
    controller: ControllerSettings


def load_config(config_path: Optional[str] = None) -> GatewayConfig:
    """
    Load and validate the gateway configuration from applications.yaml.

    Resolution order for config file:
    1. The path given as an argument.
    2. The CONFIG_PATH environment variable.
    3. config/applications.yaml relative to the current working directory.
    4. The config/ directory next to this file's package.
    """
    if config_path is None:
        config_path = os.getenv("CONFIG_PATH")

    if config_path is None:
        candidates = [
            Path.cwd() / "config" / "applications.yaml",
            Path(__file__).parent.parent / "config" / "applications.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path is None or not Path(config_path).exists():
        raise FileNotFoundError(
            "Could not locate applications.yaml. "
            "Set CONFIG_PATH or run from the project root directory."
        )

    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    # Allow environment variables to override the base URLs (useful for Docker)
    apps_raw = raw.get("applications", {})
    for app_id, app_data in apps_raw.items():
        env_key = f"{app_id.upper()}_BASE_URL"
        env_url = os.getenv(env_key)
        if env_url:
            app_data["base_url"] = env_url
        # In Docker, prefer docker_base_url if present and no env override
        elif app_data.get("docker_base_url") and os.getenv("RUNNING_IN_DOCKER"):
            app_data["base_url"] = app_data["docker_base_url"]

    # Override admin key from environment
    gw_raw = raw.get("gateway", {})
    env_key = os.getenv("ADMIN_API_KEY")
    if env_key:
        gw_raw["admin_api_key"] = env_key

    # Override mode from environment
    env_mode = os.getenv("GATEWAY_MODE")
    if env_mode:
        gw_raw["mode"] = env_mode

    config = GatewayConfig(
        applications=apps_raw,
        gateway=gw_raw,
        controller=raw.get("controller", {}),
    )

    # Validate that at least one application is enabled
    enabled = [a for a in config.applications.values() if a.enabled]
    if not enabled:
        raise ValueError("No enabled applications found in configuration.")

    return config
