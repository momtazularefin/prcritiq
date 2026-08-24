"""Trace linkage for review runs.

LangGraph already emits LangSmith traces when the environment is configured, so
the work here is opening a root run and capturing its id, which is what makes a
stored run row point at something a person can actually open.

Tracing is off unless it is both selected and credentialed. It never falls back
to a fabricated id: a run that records a trace id nobody can resolve is worse
than one that honestly records none.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class TraceHandle:
    """What a run should record about its own trace."""

    provider: str
    trace_id: str | None
    enabled: bool

    @property
    def stored_provider(self) -> str | None:
        return self.provider if self.enabled else None


_DISABLED = TraceHandle(provider="none", trace_id=None, enabled=False)


def tracing_is_available(settings: Settings) -> bool:
    """True when tracing is both requested and credentialed."""

    return settings.trace_provider.strip().lower() == "langsmith" and bool(
        settings.langsmith_api_key
    )


@contextmanager
def trace_run(settings: Settings, name: str = "prcritiq.review") -> Iterator[TraceHandle]:
    """Open a traced root run when tracing is configured, otherwise do nothing."""

    if not tracing_is_available(settings):
        yield _DISABLED
        return

    from langsmith.run_helpers import trace

    with trace(name=name, project_name=settings.langsmith_project) as run_tree:
        yield TraceHandle(provider="langsmith", trace_id=str(run_tree.id), enabled=True)
