"""Temporary repository snapshots for context retrieval.

PRCritiq downloads a source archive at an exact SHA rather than cloning, so
there is no git binary to depend on and no working tree to keep in sync. Pull
request archives are untrusted input, so extraction is bounded and every member
path is checked before anything is written. Nothing is ever written outside the
workspace root.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Settings
from .guardrails import is_indexable_path


class WorkspaceError(RuntimeError):
    """Raised when an archive cannot be turned into a safe snapshot."""


@contextmanager
def temporary_workspace(root: Path | None = None) -> Iterator[Path]:
    """Yield a workspace directory and remove it afterwards."""

    created = Path(tempfile.mkdtemp(prefix="prcritiq-", dir=str(root) if root else None))
    try:
        yield created
    finally:
        shutil.rmtree(created, ignore_errors=True)


def _member_relative_path(name: str) -> PurePosixPath | None:
    """Strip the archive top-level directory and reject anything unsafe."""

    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return None
    parts = pure.parts[1:]  # GitHub archives nest everything under owner-repo-sha/
    if not parts:
        return None
    return PurePosixPath(*parts)


def extract_source_archive(
    archive: Path,
    destination: Path,
    settings: Settings,
) -> dict[str, str]:
    """Extract the indexable source files of a repository archive.

    Only files the guardrails consider indexable are written, which keeps the
    snapshot to reviewable source instead of the whole repository, and the
    extraction is bounded by file count and total bytes so a hostile archive
    cannot exhaust the disk.

    Members are read individually rather than through `extractall`, so the
    archive never drives a filesystem write directly. Only regular files are
    considered, which drops symlinks, hardlinks, and device entries; member
    names that are absolute or contain `..` are rejected; and every resolved
    target is confirmed to sit under the workspace root before the write.
    """

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str] = {}
    written_bytes = 0
    written_files = 0

    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            for member in bundle:
                if not member.isfile():
                    continue
                relative = _member_relative_path(member.name)
                if relative is None:
                    continue
                path = str(relative)
                if not is_indexable_path(path):
                    continue
                if member.size > settings.max_file_bytes:
                    continue

                written_files += 1
                written_bytes += member.size
                if written_files > settings.max_archive_files:
                    raise WorkspaceError(
                        f"Archive holds more than {settings.max_archive_files} indexable files"
                    )
                if written_bytes > settings.max_archive_bytes:
                    raise WorkspaceError(
                        f"Archive exceeds the {settings.max_archive_bytes}-byte extraction budget"
                    )

                target = (destination / relative).resolve()
                if not target.is_relative_to(destination):
                    raise WorkspaceError(f"Archive member escapes the workspace: {member.name}")

                extracted = bundle.extractfile(member)
                if extracted is None:
                    continue
                payload = extracted.read()
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                sources[path] = payload.decode("utf-8", errors="replace")
    except tarfile.TarError as exc:
        raise WorkspaceError(f"Could not read the repository archive: {exc}") from exc

    return sources


@dataclass(frozen=True)
class RepositorySnapshot:
    """Indexable source of one repository at one commit."""

    repo: str
    ref: str
    root: Path
    sources: dict[str, str]

    @property
    def file_count(self) -> int:
        return len(self.sources)
