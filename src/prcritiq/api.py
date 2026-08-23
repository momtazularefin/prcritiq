"""FastAPI application for PRCritiq."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, status

from . import __version__
from .config import Settings, load_settings
from .reporting import build_scaffold_report
from .schemas import (
    IMPLEMENTATION_STATUS,
    HealthResponse,
    ReviewReport,
    ReviewRequest,
    WebhookAck,
)
from .webhooks import (
    SUPPORTED_PULL_REQUEST_ACTIONS,
    WebhookPayloadError,
    build_review_run_key_from_payload,
    verify_webhook_signature,
)


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
            implementation_status=IMPLEMENTATION_STATUS,
        )

    @app.post("/demo/review", response_model=ReviewReport)
    def demo_review(request: ReviewRequest) -> ReviewReport:
        return build_scaffold_report(repo=str(request.repo), pr_number=request.pr_number)

    @app.post("/webhooks/github", response_model=WebhookAck)
    async def github_webhook(
        request: Request,
        x_hub_signature_256: Annotated[str | None, Header()] = None,
        x_github_event: Annotated[str | None, Header()] = None,
        x_github_delivery: Annotated[str | None, Header()] = None,
    ) -> WebhookAck:
        body = await request.body()
        if not resolved.github_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GITHUB_WEBHOOK_SECRET is not configured",
            )
        if not verify_webhook_signature(
            secret=resolved.github_webhook_secret,
            body=body,
            signature_header=x_hub_signature_256,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

        event = x_github_event or "unknown"
        delivery_id = x_github_delivery or "unknown"
        payload = await request.json()
        action = payload.get("action") if isinstance(payload, dict) else None

        if event != "pull_request":
            return WebhookAck(
                accepted=False,
                event=event,
                delivery_id=delivery_id,
                action=action,
                message=f"Ignored unsupported GitHub event: {event}",
            )
        if action not in SUPPORTED_PULL_REQUEST_ACTIONS:
            return WebhookAck(
                accepted=False,
                event=event,
                delivery_id=delivery_id,
                action=action,
                message=f"Ignored unsupported pull_request action: {action}",
            )

        try:
            idempotency_key = build_review_run_key_from_payload(payload, delivery_id=delivery_id)
        except WebhookPayloadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        return WebhookAck(
            accepted=True,
            event=event,
            delivery_id=delivery_id,
            action=action,
            idempotency_key=idempotency_key,
            message="Pull request event accepted for a future review run.",
        )

    return app


app = create_app()
