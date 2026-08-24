"""Configuration for PRCritiq."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from os import getenv


class SimilarityMode(StrEnum):
    """How retrieval ranks related source chunks."""

    LEXICAL = "lexical"
    EMBEDDING = "embedding"


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


def parse_similarity(value: str | None) -> SimilarityMode:
    """Parse PRCRITIQ_SIMILARITY without silent fallback."""

    normalized = (value or SimilarityMode.LEXICAL.value).strip().lower()
    try:
        return SimilarityMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in SimilarityMode)
        raise ConfigError(f"PRCRITIQ_SIMILARITY must be one of: {allowed}") from exc


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
    similarity: SimilarityMode = SimilarityMode.LEXICAL
    max_context_chunks: int = 24
    max_context_bytes: int = 200_000
    max_archive_bytes: int = 80_000_000
    max_archive_files: int = 20_000
    github_webhook_secret: str | None = None
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_request_timeout_seconds: int = 15


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
        similarity=parse_similarity(getenv("PRCRITIQ_SIMILARITY")),
        max_context_chunks=_int_from_env("PRCRITIQ_MAX_CONTEXT_CHUNKS", 24),
        max_context_bytes=_int_from_env("PRCRITIQ_MAX_CONTEXT_BYTES", 200_000),
        max_archive_bytes=_int_from_env("PRCRITIQ_MAX_ARCHIVE_BYTES", 80_000_000),
        max_archive_files=_int_from_env("PRCRITIQ_MAX_ARCHIVE_FILES", 20_000),
        github_webhook_secret=getenv("GITHUB_WEBHOOK_SECRET") or None,
        github_token=getenv("GITHUB_TOKEN") or None,
        github_api_base_url=getenv("GITHUB_API_BASE_URL", "https://api.github.com"),
        github_request_timeout_seconds=_int_from_env("GITHUB_REQUEST_TIMEOUT_SECONDS", 15),
    )
