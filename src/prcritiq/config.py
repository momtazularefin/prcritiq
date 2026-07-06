"""Configuration for the PRCritiq scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from os import getenv


class AccelerationMode(StrEnum):
    """Hardware acceleration modes with strict no-fallback semantics."""

    NONE = "none"
    GPU = "gpu"
    NPU = "npu"


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def parse_acceleration(value: str | None) -> AccelerationMode:
    """Parse ACCELERATION without silent fallback."""

    normalized = (value or AccelerationMode.NONE.value).strip().lower()
    try:
        return AccelerationMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in AccelerationMode)
        raise ConfigError(f"ACCELERATION must be one of: {allowed}") from exc


def _int_from_env(name: str, default: int) -> int:
    value = getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    env: str = "local"
    public_base_url: str = "http://localhost:8000"
    default_mode: str = "dry-run"
    model_policy: str = "auto"
    max_files: int = 30
    max_diff_lines: int = 2000
    max_file_bytes: int = 250_000
    min_publish_confidence: int = 78
    trace_provider: str = "none"
    acceleration: AccelerationMode = AccelerationMode.NONE


def load_settings() -> Settings:
    """Load settings from the process environment."""

    return Settings(
        env=getenv("PRCRITIQ_ENV", "local"),
        public_base_url=getenv("PRCRITIQ_PUBLIC_BASE_URL", "http://localhost:8000"),
        default_mode=getenv("PRCRITIQ_DEFAULT_MODE", "dry-run"),
        model_policy=getenv("PRCRITIQ_MODEL_POLICY", "auto"),
        max_files=_int_from_env("PRCRITIQ_MAX_FILES", 30),
        max_diff_lines=_int_from_env("PRCRITIQ_MAX_DIFF_LINES", 2000),
        max_file_bytes=_int_from_env("PRCRITIQ_MAX_FILE_BYTES", 250_000),
        min_publish_confidence=_int_from_env("PRCRITIQ_MIN_PUBLISH_CONFIDENCE", 78),
        trace_provider=getenv("PRCRITIQ_TRACE_PROVIDER", "none"),
        acceleration=parse_acceleration(getenv("ACCELERATION")),
    )
