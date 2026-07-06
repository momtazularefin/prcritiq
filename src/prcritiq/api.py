"""FastAPI application for the PRCritiq M0 scaffold."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .config import Settings, load_settings
from .reporting import build_scaffold_report
from .schemas import HealthResponse, ReviewReport, ReviewRequest


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI app."""

    resolved = settings or load_settings()
    app = FastAPI(
        title="PRCritiq",
        summary="Evidence-backed pull request review agent.",
        version=__version__,
    )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            service="prcritiq",
            status="ok",
            version=__version__,
            environment=resolved.env,
            acceleration=resolved.acceleration.value,
            implementation_status="m0_scaffold",
        )

    @app.post("/demo/review", response_model=ReviewReport)
    def demo_review(request: ReviewRequest) -> ReviewReport:
        return build_scaffold_report(repo=str(request.repo), pr_number=request.pr_number)

    return app


app = create_app()
