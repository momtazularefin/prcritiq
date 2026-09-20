from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from prcritiq.github import GitHubClient
from prcritiq.verification_sources import (
    VerificationSourceError,
    fetch_sources,
    validate_source_request,
)

SHA = "a1" * 20


def test_public_preflight_returns_stable_unique_paths_without_a_client() -> None:
    assert validate_source_request(
        "example/repo", SHA.upper(), ["b.py", "a.py", "b.py"], 100, 200
    ) == ("b.py", "a.py")
    assert validate_source_request("example/repo", SHA, [], 100, 200) == ()


def test_public_preflight_checks_later_paths_and_accepts_exact_path_limit() -> None:
    paths = [f"source{index}.py" for index in range(30)]
    assert validate_source_request("example/repo", SHA, paths, 100, 200) == tuple(paths)
    with pytest.raises(VerificationSourceError, match="normalized, safe, indexable"):
        validate_source_request("example/repo", SHA, ["safe.py", "../bad.py"], 100, 200)
    with pytest.raises(VerificationSourceError, match="40-hex"):
        validate_source_request("example/repo", "main", paths, 100, 200)
    with pytest.raises(VerificationSourceError, match="positive integers"):
        validate_source_request("example/repo", SHA, paths, 100, 0)


class Chunks(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.read_chunks = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.read_chunks += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def test_fetches_pinned_raw_unicode_source_once_on_configured_origin() -> None:
    calls: list[httpx.Request] = []
    path = "src/é space#?.py"
    source = "message = 'বাংলা'\n"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "github.example.test"
        assert request.url.path == f"/api/v3/repos/example/repo/contents/{path}"
        assert dict(request.url.params) == {"ref": SHA.upper()}
        assert b"%C3%A9%20space%23%3F.py" in request.url.raw_path
        assert request.headers["Accept"] == "application/vnd.github.raw+json"
        assert request.headers["Authorization"] == "Bearer private-token"
        payload = source.encode("utf-8")
        return httpx.Response(200, stream=Chunks(payload[:14], payload[14:]))

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://github.example.test/api/v3",
        headers={"Authorization": "Bearer private-token"},
    ) as http_client:
        result = fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA.upper(),
            paths=[path, path],
        )
    assert result == {path: source}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../app.py",
        "src/../app.py",
        "./app.py",
        "src/./app.py",
        "src//app.py",
        "/app.py",
        "src/app.py/",
        "src\\app.py",
        "C:app.py",
        "C:/app.py",
        "src/a\x00.py",
        "src/a\n.py",
        "src/a\x7f.py",
        "src/a\u0085.py",
        "src/a\u202e.py",
        "src/a\ud800.py",
        ".git/app.py",
        "node_modules/app.js",
        "image.png",
        "README.md",
        123,
    ],
)
def test_all_paths_are_validated_before_any_request(path: object) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"source")

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.github.test"
        ) as http_client,
        pytest.raises(VerificationSourceError, match="normalized, safe, indexable"),
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["safe.py", path],  # type: ignore[list-item]
        )
    assert calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"ref": "main"},
        {"ref": "a" * 39},
        {"ref": "a" * 41},
        {"ref": "g" * 40},
        {"ref": SHA + "\n"},
        {"ref": None},
        {"repo": "https://github.com/example/repo"},
        {"repo": "example/repo/extra"},
        {"repo": "example//repo"},
        {"repo": "example/repo.git"},
        {"repo": "../repo"},
        {"repo": "example/.."},
        {"repo": " example/repo "},
        {"repo": "private-token"},
        {"repo": None},
        {"paths": "app.py"},
        {"paths": ["app.py"] * 31},
        {"max_file_bytes": 0},
        {"max_file_bytes": True},
        {"max_total_bytes": -1},
        {"max_total_bytes": 1.5},
    ],
)
def test_invalid_options_fail_without_network(overrides: dict[str, object]) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"source")

    options = dict(repo="example/repo", ref=SHA, paths=["app.py"])
    options.update(overrides)
    with (
        httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.github.test"
        ) as http_client,
        pytest.raises(VerificationSourceError) as caught,
    ):
        fetch_sources(client=GitHubClient(http_client=http_client), **options)
    assert "private-token" not in str(caught.value)
    assert calls == []


def test_404_is_omitted_and_empty_file_is_retained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path.endswith("missing.py") else 200)

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.github.test"
    ) as http_client:
        assert fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["missing.py", "empty.py"],
        ) == {"empty.py": ""}


@pytest.mark.parametrize("chunks", [(b"123456", b"not-read"), (b"123", b"456", b"not-read")])
def test_per_file_budget_stops_stream_before_decoding(chunks: tuple[bytes, ...]) -> None:
    stream = Chunks(*chunks)
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
            base_url="https://api.github.test",
        ) as http_client,
        pytest.raises(VerificationSourceError, match="per-file byte limit"),
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
            max_file_bytes=5,
        )
    assert stream.read_chunks == len(chunks) - 1
    assert stream.closed


def test_total_byte_budget_applies_across_files_and_stops_before_later_request() -> None:
    paths: list[str] = []
    streams: list[Chunks] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        stream = Chunks(b"123")
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.github.test"
        ) as http_client,
        pytest.raises(VerificationSourceError, match="total byte limit"),
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["one.py", "two.py", "three.py"],
            max_file_bytes=3,
            max_total_bytes=5,
        )
    assert len(paths) == 2
    assert all(stream.closed for stream in streams)


def test_exact_byte_budget_and_utf8_multibyte_boundaries() -> None:
    payload = "é".encode()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=Chunks(payload[:1], payload[1:]))
        ),
        base_url="https://api.github.test",
    ) as http_client:
        assert fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
            max_file_bytes=2,
            max_total_bytes=2,
        ) == {"app.py": "é"}


def test_invalid_utf8_is_rejected_without_source_in_error() -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"secret\xff")),
            base_url="https://api.github.test",
        ) as http_client,
        pytest.raises(VerificationSourceError, match="not valid UTF-8") as caught,
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
        )
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 307, 403, 500])
def test_status_failures_are_sanitized_and_redirects_never_followed(status: int) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            content=b"private-token secret body",
            headers={"Location": "https://evil.test/private-token"},
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.github.test",
            follow_redirects=True,
            headers={"Authorization": "Bearer private-token"},
        ) as http_client,
        pytest.raises(VerificationSourceError, match=f"status {status}") as caught,
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
        )
    assert len(calls) == 1
    assert calls[0].url.host == "api.github.test"
    assert "private-token" not in str(caught.value)
    assert "secret body" not in str(caught.value)


def test_network_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-token secret-url", request=request)

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.github.test"
        ) as http_client,
        pytest.raises(VerificationSourceError, match="HTTP boundary") as caught,
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
        )
    assert "private-token" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("payload", [{"type": "symlink", "target": "../secret"}, []])
def test_metadata_is_not_treated_as_source(payload: object) -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
            base_url="https://api.github.test",
        ) as http_client,
        pytest.raises(VerificationSourceError, match="not raw file content"),
    ):
        fetch_sources(
            client=GitHubClient(http_client=http_client),
            repo="example/repo",
            ref=SHA,
            paths=["app.py"],
        )
