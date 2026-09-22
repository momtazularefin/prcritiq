"""Configuration for PRCritiq."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from os import getenv

from dotenv import load_dotenv


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


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _bool_from_env(name: str, default: bool) -> bool:
    """Parse a boolean flag, refusing anything that is not clearly on or off.

    A typo such as `ture` must not silently leave a safety switch in whichever
    state the default happens to be.
    """

    value = getenv(name)
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise ConfigError(f"{name} must be one of: true, false (got {value!r})")


def _pem_from_env(name: str) -> str | None:
    """Read a PEM block that may arrive with literal backslash-n separators.

    Env files and container secret stores often carry a private key on one
    line; the escaped form is restored so the key parses either way.
    """

    value = getenv(name)
    if not value or not value.strip():
        return None
    return value.replace("\\n", "\n").strip() + "\n"


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
    tool_timeout_seconds: int = 60
    max_tool_output_bytes: int = 200_000
    anthropic_model: str = "claude-opus-5"
    model_effort: str = "low"
    openai_model: str = "gpt-5.6-terra"
    anthropic_api_key: str | None = None
    anthropic_workspace_id: str | None = None
    database_url: str | None = None
    langsmith_api_key: str | None = None
    langsmith_project: str = "prcritiq"
    openai_api_key: str | None = None
    github_webhook_secret: str | None = None
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_request_timeout_seconds: int = 15
    github_app_id: str | None = None
    github_private_key: str | None = None
    # Webhook runs are dry-run: they are recorded, never posted. Model review is
    # opt-in because a public webhook URL must not spend credit by default.
    webhook_review: bool = False
    # A public deployment reviews public repositories only. Opting in exposes
    # private repository names through run status, so it is an explicit choice.
    allow_private_repos: bool = False
    demo_enabled: bool = True
    demo_requests_per_minute: int = 6


def load_settings() -> Settings:
    """Load settings from the process environment, and from `.env` if present.

    A real environment variable always wins over the file, so an explicit export
    or a CI secret is never silently overridden by a stale local `.env`.
    """

    load_dotenv(override=False)
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
        tool_timeout_seconds=_int_from_env("PRCRITIQ_TOOL_TIMEOUT_SECONDS", 60),
        max_tool_output_bytes=_int_from_env("PRCRITIQ_MAX_TOOL_OUTPUT_BYTES", 200_000),
        anthropic_model=getenv("PRCRITIQ_ANTHROPIC_MODEL", "claude-opus-5"),
        model_effort=getenv("PRCRITIQ_MODEL_EFFORT", "low"),
        openai_model=getenv("PRCRITIQ_OPENAI_MODEL", "gpt-5.6-terra"),
        anthropic_api_key=getenv("ANTHROPIC_API_KEY") or None,
        anthropic_workspace_id=getenv("ANTHROPIC_WORKSPACE_ID") or None,
        database_url=getenv("DATABASE_URL") or None,
        langsmith_api_key=getenv("LANGSMITH_API_KEY") or None,
        langsmith_project=getenv("LANGSMITH_PROJECT", "prcritiq"),
        openai_api_key=getenv("OPENAI_API_KEY") or None,
        github_webhook_secret=getenv("GITHUB_WEBHOOK_SECRET") or None,
        github_token=getenv("GITHUB_TOKEN") or None,
        github_api_base_url=getenv("GITHUB_API_BASE_URL", "https://api.github.com"),
        github_request_timeout_seconds=_int_from_env("GITHUB_REQUEST_TIMEOUT_SECONDS", 15),
        github_app_id=getenv("GITHUB_APP_ID") or None,
        github_private_key=_pem_from_env("GITHUB_PRIVATE_KEY"),
        webhook_review=_bool_from_env("PRCRITIQ_WEBHOOK_REVIEW", False),
        allow_private_repos=_bool_from_env("PRCRITIQ_ALLOW_PRIVATE_REPOS", False),
        demo_enabled=_bool_from_env("PRCRITIQ_DEMO_ENABLED", True),
        demo_requests_per_minute=_int_from_env("PRCRITIQ_DEMO_REQUESTS_PER_MINUTE", 6),
    )
