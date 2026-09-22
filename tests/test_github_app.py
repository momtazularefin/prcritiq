"""Tests for GitHub App authentication. No test reaches GitHub."""

from __future__ import annotations

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from prcritiq.config import Settings
from prcritiq.github_app import (
    AppCredentials,
    GitHubAppAuthError,
    app_credentials,
    build_app_jwt,
    client_for_installation,
    create_installation_token,
)


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


@pytest.fixture
def credentials(key_pair) -> AppCredentials:
    return AppCredentials(app_id="12345", private_key=key_pair[0])


def test_the_app_jwt_is_short_lived_and_names_the_app(credentials, key_pair) -> None:
    token = build_app_jwt(credentials, now=1_000_000)

    claims = jwt.decode(
        token,
        key_pair[1],
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == "12345"
    assert claims["iat"] == 1_000_000 - 60
    assert claims["exp"] - claims["iat"] <= 600


def test_a_key_that_is_not_a_pem_key_is_named() -> None:
    with pytest.raises(GitHubAppAuthError, match="PEM"):
        build_app_jwt(AppCredentials(app_id="1", private_key="not a key"))


def test_half_configured_credentials_are_refused() -> None:
    with pytest.raises(GitHubAppAuthError, match="both"):
        app_credentials(Settings(github_app_id="12345"))


def test_no_credentials_means_no_app_authentication() -> None:
    assert app_credentials(Settings()) is None


def _transport(status_code: int, body: dict, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json=body)

    return httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(handler))


def test_an_installation_token_is_exchanged_with_the_app_jwt(credentials) -> None:
    seen: list[httpx.Request] = []

    token = create_installation_token(
        credentials, 777, http_client=_transport(201, {"token": "ghs_example"}, seen)
    )

    assert token == "ghs_example"
    assert seen[0].url.path == "/app/installations/777/access_tokens"
    assert seen[0].headers["authorization"].startswith("Bearer ey")


def test_a_refused_exchange_raises_instead_of_falling_back(credentials) -> None:
    with pytest.raises(GitHubAppAuthError, match="401"):
        create_installation_token(
            credentials, 777, http_client=_transport(401, {"message": "Bad credentials"}, [])
        )


def test_app_credentials_without_an_installation_are_refused(key_pair) -> None:
    settings = Settings(github_app_id="12345", github_private_key=key_pair[0])

    with pytest.raises(GitHubAppAuthError, match="installation id"):
        client_for_installation(settings, None)


def test_without_app_credentials_the_configured_token_is_used() -> None:
    client = client_for_installation(Settings(github_token="ghp_example"), 777)
    try:
        assert client._client.headers["authorization"] == "Bearer ghp_example"
    finally:
        client.close()
