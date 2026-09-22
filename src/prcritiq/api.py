"""FastAPI application for PRCritiq."""

from __future__ import annotations

from typing import Annotated

import psycopg
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import Settings, load_settings
from .github import GitHubClientError, PrivateRepositoryError, RepoReferenceError
from .github_app import client_for_installation
from .intake import (
    ClientFactory,
    accept_event,
    execute_run,
    parse_pull_request_event,
    run_status,
)
from .providers import ModelProvider
from .ratelimit import SlidingWindowLimiter
from .review import run_dry_run
from .schemas import (
    IMPLEMENTATION_STATUS,
    HealthResponse,
    ReviewReport,
    ReviewRequest,
    RunStatusResponse,
    WebhookAck,
)
from .store import StoreError
from .webhooks import (
    SUPPORTED_PULL_REQUEST_ACTIONS,
    WebhookPayloadError,
    verify_webhook_signature,
)


def create_app(
    settings: Settings | None = None,
    *,
    github_client_factory: ClientFactory | None = None,
    provider: ModelProvider | None = None,
) -> FastAPI:
    """Create the FastAPI app.

    The client factory and provider are injectable so webhook runs can be
    exercised end to end in tests without GitHub or a model (NFR4).
    """

    resolved = settings or load_settings()
    client_factory = github_client_factory or client_for_installation
    # Zero or less turns the limit off, which suits a local run and nothing else.
    demo_limiter = (
        SlidingWindowLimiter(resolved.demo_requests_per_minute)
        if resolved.demo_requests_per_minute > 0
        else None
    )
    app = FastAPI(
        title="PRCritiq",
        summary="Evidence-backed pull request review agent.",
        version=__version__,
    )

    # HEAD too, because uptime monitors commonly probe with it.
    @app.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
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
    def demo_review(payload: ReviewRequest, request: Request) -> ReviewReport:
        if not resolved.demo_enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The demo endpoint is disabled on this deployment",
            )
        if demo_limiter is not None:
            retry_after = demo_limiter.acquire(_client_address(request))
            if retry_after is not None:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many demo reviews from this address; try again shortly",
                    headers={"Retry-After": str(retry_after)},
                )
        try:
            return run_dry_run(
                repo=str(payload.repo),
                pr_number=payload.pr_number,
                settings=resolved,
                public_only=not resolved.allow_private_repos,
            )
        except RepoReferenceError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PrivateRepositoryError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except GitHubClientError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @app.post("/webhooks/github", response_model=WebhookAck)
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
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

        event_name = x_github_event or "unknown"
        delivery_id = x_github_delivery or "unknown"
        payload = await request.json()
        action = payload.get("action") if isinstance(payload, dict) else None

        def ignored(message: str) -> WebhookAck:
            return WebhookAck(
                accepted=False,
                event=event_name,
                delivery_id=delivery_id,
                action=action,
                message=message,
            )

        if event_name != "pull_request":
            return ignored(f"Ignored unsupported GitHub event: {event_name}")
        if action not in SUPPORTED_PULL_REQUEST_ACTIONS:
            return ignored(f"Ignored unsupported pull_request action: {action}")

        try:
            event = parse_pull_request_event(payload, delivery_id=delivery_id)
        except WebhookPayloadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        if event.repo_private and not resolved.allow_private_repos:
            return ignored("Ignored a private repository: this deployment reviews public ones only")
        if not resolved.database_url:
            # Refusing, rather than acknowledging and dropping, lets GitHub show
            # the delivery as failed so it can be redelivered once fixed.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DATABASE_URL is not configured; webhook runs cannot be recorded",
            )

        try:
            accepted = await run_in_threadpool(accept_event, resolved, event)
        except (StoreError, psycopg.Error) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The run store is unavailable; redeliver this event later",
            ) from exc

        if accepted.created:
            background_tasks.add_task(
                execute_run,
                resolved,
                event,
                accepted.run_id,
                client_factory=client_factory,
                provider=provider,
            )
            message = f"Accepted as dry-run review run {accepted.run_id}; nothing will be posted."
        else:
            message = (
                f"This delivery is already run {accepted.run_id} ({accepted.status}); "
                "it was not run again."
            )

        return WebhookAck(
            accepted=True,
            event=event_name,
            delivery_id=delivery_id,
            action=action,
            idempotency_key=event.idempotency_key,
            run_id=accepted.run_id,
            run_status=accepted.status,
            message=message,
        )

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    def get_run_status(run_id: int) -> RunStatusResponse:
        if not resolved.database_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="DATABASE_URL is not configured; there are no recorded runs",
            )
        try:
            row = run_status(resolved, run_id)
        except (StoreError, psycopg.Error) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The run store is unavailable",
            ) from exc
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such run")
        return RunStatusResponse(
            run_id=int(row["id"]),
            repo=str(row["repo_full_name"]),
            pr_number=int(row["pr_number"]),
            head_sha=str(row["head_sha"]),
            mode=str(row["mode"]),
            status=str(row["status"]),
            summary=row.get("summary"),
            error=row.get("error"),
            files=row["files"],
            reviewable_files=row["reviewable_files"],
            findings=row["findings"],
            publishable_findings=row["publishable_findings"],
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
        )

    return app


def _client_address(request: Request) -> str:
    """The caller's address as the server sees it.

    Behind the deployment's reverse proxy this is the forwarded client address,
    because uvicorn runs with proxy headers trusted from that proxy only.
    """

    return request.client.host if request.client else "unknown"


app = create_app()
