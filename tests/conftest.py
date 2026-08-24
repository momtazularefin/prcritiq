"""Shared fixtures.

The dry-run path is exercised end to end against a stub GitHub client so CI
never needs a network call or a token (NFR4).
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Sequence

import pytest

from prcritiq.github import ChangedFile, PullRequestMetadata

SAMPLE_PATCH = "@@ -1,3 +1,5 @@\n import os\n \n+VALUE = 1\n+OTHER = 2\n print(os)\n"


@pytest.fixture
def pr_metadata() -> PullRequestMetadata:
    return PullRequestMetadata(
        repo="example/repo",
        number=7,
        title="Add configuration values",
        state="open",
        base_sha="base111",
        head_sha="head222",
        author_login="octocat",
        html_url="https://github.com/example/repo/pull/7",
    )


@pytest.fixture
def changed_files() -> list[ChangedFile]:
    """A mixed PR: one reviewable Python file, one lockfile, one unsupported doc."""

    return [
        ChangedFile(
            filename="src/app.py",
            status="modified",
            additions=2,
            deletions=0,
            changes=2,
            patch=SAMPLE_PATCH,
        ),
        ChangedFile(
            filename="uv.lock",
            status="modified",
            additions=40,
            deletions=10,
            changes=50,
            patch="@@ -1,1 +1,2 @@\n locked\n+more\n",
        ),
        ChangedFile(
            filename="README.md",
            status="modified",
            additions=1,
            deletions=0,
            changes=1,
            patch="@@ -1,1 +1,2 @@\n title\n+line\n",
        ),
    ]


class StubGitHubClient:
    """Stands in for GitHubClient without touching the network."""

    def __init__(
        self,
        metadata: PullRequestMetadata,
        files: Sequence[ChangedFile],
    ) -> None:
        self.metadata = metadata
        self.files = list(files)
        self.calls: list[tuple[str, str, int]] = []
        self.closed = False

    def get_pull_request(self, repo: str, number: int) -> PullRequestMetadata:
        self.calls.append(("get_pull_request", repo, number))
        return self.metadata

    def list_changed_files(self, repo: str, number: int) -> list[ChangedFile]:
        self.calls.append(("list_changed_files", repo, number))
        return list(self.files)

    def download_source_archive(self, repo: str, ref: str, destination):
        """Write a tarball of the fixture repository, as GitHub would serve one."""

        self.calls.append(("download_source_archive", repo, ref))
        with tarfile.open(destination, "w:gz") as bundle:
            for path, text in REPO_SOURCES.items():
                payload = text.encode("utf-8")
                info = tarfile.TarInfo(f"example-repo-{ref}/{path}")
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        return destination

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> StubGitHubClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@pytest.fixture
def stub_client(
    pr_metadata: PullRequestMetadata,
    changed_files: list[ChangedFile],
) -> StubGitHubClient:
    return StubGitHubClient(pr_metadata, changed_files)


@pytest.fixture
def patched_github(
    monkeypatch: pytest.MonkeyPatch, stub_client: StubGitHubClient
) -> StubGitHubClient:
    """Replace the client the review path constructs for itself."""

    monkeypatch.setattr("prcritiq.review.GitHubClient", lambda **_kwargs: stub_client)
    return stub_client


REPO_SOURCES: dict[str, str] = {
    "src/app/service.py": (
        '"""Order service."""\n'
        "\n"
        "from app.models import Order\n"
        "from app.notify import send_receipt\n"
        "\n"
        "RETRY_LIMIT = 3\n"
        "\n"
        "\n"
        "def submit_order(order: Order) -> bool:\n"
        "    if not order.is_valid():\n"
        "        return False\n"
        "    send_receipt(order)\n"
        "    return True\n"
    ),
    "src/app/models.py": (
        '"""Domain models."""\n'
        "\n"
        "\n"
        "class Order:\n"
        "    def __init__(self, total):\n"
        "        self.total = total\n"
        "\n"
        "    def is_valid(self) -> bool:\n"
        "        checked = self.total > 0\n"
        "        return checked\n"
    ),
    "src/app/notify.py": (
        '"""Notifications."""\n'
        "\n"
        "\n"
        "def send_receipt(order):\n"
        '    payload = {"total": order.total}\n'
        "    return payload\n"
    ),
    "src/app/pricing.py": (
        '"""Pricing rules unrelated to submission."""\n'
        "\n"
        "\n"
        "def apply_discount(total, percent):\n"
        "    reduced = total - (total * percent)\n"
        "    return reduced\n"
    ),
    "src/reporting/summary.py": (
        '"""Reporting lives in another package."""\n'
        "\n"
        "\n"
        "def summarize_submitted_order(order):\n"
        "    submitted = order.is_valid()\n"
        '    return {"submitted": submitted}\n'
    ),
    "tests/test_service.py": (
        "from app.service import submit_order\n"
        "\n"
        "\n"
        "def test_submit_order_rejects_invalid():\n"
        "    assert submit_order(FakeOrder(0)) is False\n"
    ),
    "tests/test_pricing.py": (
        "from app.pricing import apply_discount\n"
        "\n"
        "\n"
        "def test_apply_discount():\n"
        "    assert apply_discount(100, 0.1) == 90\n"
    ),
    "vendor/left_pad/index.py": "def left_pad(value):\n    return value\n",
    "docs/guide.md": "# Guide\n",
}


@pytest.fixture
def repo_sources() -> dict[str, str]:
    """A small synthetic repository used as a retrieval regression fixture."""

    return dict(REPO_SOURCES)
