"""GitHub App authentication: app JWT, then a short-lived installation token.

A GitHub App authenticates in two steps. It signs a JWT with its private key to
prove it is the app, then exchanges that JWT for an installation token scoped
to the repositories one installation granted. The installation token is what
reads pull requests; the private key never leaves this process.

Authentication failures raise rather than falling back to a weaker credential.
A webhook that quietly switched to anonymous access would fail later with a
confusing 404 on a private repository, or succeed against a rate limit it was
never meant to share.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt

from .config import Settings
from .github import GitHubClient, GitHubClientError

#: GitHub rejects app JWTs that live longer than ten minutes. Issuing slightly
#: in the past absorbs clock drift between this host and GitHub.
_JWT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540


class GitHubAppAuthError(GitHubClientError):
    """Raised when the app cannot authenticate as itself or its installation."""


@dataclass(frozen=True)
class AppCredentials:
    app_id: str
    private_key: str


def app_credentials(settings: Settings) -> AppCredentials | None:
    """Return app credentials when both halves are configured.

    Configuring only one half is a mistake worth stopping on, not a signal to
    run without app authentication.
    """

    if settings.github_app_id and settings.github_private_key:
        return AppCredentials(settings.github_app_id, settings.github_private_key)
    if settings.github_app_id or settings.github_private_key:
        raise GitHubAppAuthError(
            "GitHub App authentication needs both GITHUB_APP_ID and GITHUB_PRIVATE_KEY; "
            "only one is set."
        )
    return None


def build_app_jwt(credentials: AppCredentials, *, now: float | None = None) -> str:
    """Sign the short-lived JWT that identifies the app itself."""

    issued = int(now if now is not None else time.time()) - _JWT_BACKDATE_SECONDS
    try:
        return jwt.encode(
            {"iat": issued, "exp": issued + _JWT_LIFETIME_SECONDS, "iss": credentials.app_id},
            credentials.private_key,
            algorithm="RS256",
        )
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GitHubAppAuthError(
            "GITHUB_PRIVATE_KEY could not sign an app JWT; check that it is the app's PEM key."
        ) from exc


def create_installation_token(
    credentials: AppCredentials,
    installation_id: int,
    *,
    api_base_url: str = "https://api.github.com",
    timeout_seconds: int = 15,
    http_client: httpx.Client | None = None,
) -> str:
    """Exchange the app JWT for a token scoped to one installation."""

    owned = http_client is None
    client = http_client or httpx.Client(base_url=api_base_url, timeout=timeout_seconds)
    try:
        response = client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {build_app_jwt(credentials)}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "PRCritiq",
            },
        )
    except httpx.HTTPError as exc:
        raise GitHubAppAuthError(f"Could not reach GitHub to mint a token: {exc}") from exc
    finally:
        if owned:
            client.close()

    if response.status_code != 201:
        raise GitHubAppAuthError(
            f"GitHub refused an installation token for installation {installation_id}: "
            f"{response.status_code}"
        )
    token = response.json().get("token")
    if not token:
        raise GitHubAppAuthError("GitHub returned an installation token response with no token")
    return str(token)


def client_for_installation(
    settings: Settings,
    installation_id: int | None,
    *,
    http_client: httpx.Client | None = None,
) -> GitHubClient:
    """Build the client a webhook run reads through.

    App credentials plus an installation id give an installation token. Without
    app credentials the deployment is running as a plain webhook receiver, and
    reads with GITHUB_TOKEN when set or anonymously for public repositories.
    """

    credentials = app_credentials(settings)
    token = settings.github_token
    if credentials is not None:
        if installation_id is None:
            raise GitHubAppAuthError(
                "GitHub App credentials are configured but the event carries no installation id"
            )
        token = create_installation_token(
            credentials,
            installation_id,
            api_base_url=settings.github_api_base_url,
            timeout_seconds=settings.github_request_timeout_seconds,
            http_client=http_client,
        )
    return GitHubClient(
        token=token,
        api_base_url=settings.github_api_base_url,
        timeout_seconds=settings.github_request_timeout_seconds,
    )
