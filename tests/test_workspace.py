"""Tests for archive extraction safety and workspace lifecycle."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from prcritiq.config import Settings
from prcritiq.workspace import (
    WorkspaceError,
    extract_source_archive,
    temporary_workspace,
)

TOP = "example-repo-abc123"


def build_archive(path: Path, members: list[tuple[str, bytes]], *, symlinks: bool = False) -> Path:
    with tarfile.open(path, "w:gz") as bundle:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        if symlinks:
            link = tarfile.TarInfo(f"{TOP}/link.py")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../../etc/passwd"
            bundle.addfile(link)
    return path


class TestExtraction:
    def test_indexable_sources_are_extracted(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [
                (f"{TOP}/src/app.py", b"x = 1\n"),
                (f"{TOP}/web/main.ts", b"export const a = 1;\n"),
            ],
        )

        sources = extract_source_archive(archive, tmp_path / "out", Settings())

        assert set(sources) == {"src/app.py", "web/main.ts"}
        assert (tmp_path / "out" / "src" / "app.py").read_text() == "x = 1\n"

    def test_top_level_directory_is_stripped(self, tmp_path: Path) -> None:
        archive = build_archive(tmp_path / "a.tar.gz", [(f"{TOP}/src/app.py", b"x = 1\n")])

        sources = extract_source_archive(archive, tmp_path / "out", Settings())

        assert "src/app.py" in sources
        assert not (tmp_path / "out" / TOP).exists()

    def test_non_source_files_are_skipped(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [
                (f"{TOP}/README.md", b"# hi\n"),
                (f"{TOP}/uv.lock", b"locked\n"),
                (f"{TOP}/node_modules/pad/index.js", b"module.exports = 1;\n"),
                (f"{TOP}/logo.png", b"\x89PNG\r\n"),
                (f"{TOP}/src/app.py", b"x = 1\n"),
            ],
        )

        sources = extract_source_archive(archive, tmp_path / "out", Settings())

        assert set(sources) == {"src/app.py"}


class TestExtractionSafety:
    @pytest.mark.parametrize(
        "member",
        [f"{TOP}/../../escape.py", "/etc/escape.py", f"{TOP}/../escape.py"],
    )
    def test_traversal_members_are_never_written(self, tmp_path: Path, member: str) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [(member, b"pwned = True\n"), (f"{TOP}/src/app.py", b"x = 1\n")],
        )

        sources = extract_source_archive(archive, tmp_path / "out", Settings())

        assert set(sources) == {"src/app.py"}
        assert not (tmp_path / "escape.py").exists()
        assert not (tmp_path.parent / "escape.py").exists()

    def test_symlink_members_are_ignored(self, tmp_path: Path) -> None:
        """Only regular files are considered, so links cannot redirect a write."""

        archive = build_archive(
            tmp_path / "a.tar.gz",
            [(f"{TOP}/src/app.py", b"x = 1\n")],
            symlinks=True,
        )

        sources = extract_source_archive(archive, tmp_path / "out", Settings())

        assert set(sources) == {"src/app.py"}
        assert not (tmp_path / "out" / "link.py").exists()

    def test_nothing_is_written_outside_the_destination(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [(f"{TOP}/../../../evil.py", b"x = 1\n"), (f"{TOP}/src/app.py", b"x = 1\n")],
        )
        destination = tmp_path / "out"

        extract_source_archive(archive, destination, Settings())

        written = {item for item in destination.rglob("*") if item.is_file()}
        assert written == {destination / "src" / "app.py"}


class TestBudgets:
    def test_file_count_budget_is_enforced(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [(f"{TOP}/src/mod_{index}.py", b"x = 1\n") for index in range(5)],
        )

        with pytest.raises(WorkspaceError, match="more than 2 indexable files"):
            extract_source_archive(archive, tmp_path / "out", Settings(max_archive_files=2))

    def test_total_byte_budget_is_enforced(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [(f"{TOP}/src/mod_{index}.py", b"x = 1\n" * 100) for index in range(5)],
        )

        with pytest.raises(WorkspaceError, match="extraction budget"):
            extract_source_archive(archive, tmp_path / "out", Settings(max_archive_bytes=200))

    def test_individual_oversized_files_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        archive = build_archive(
            tmp_path / "a.tar.gz",
            [
                (f"{TOP}/src/huge.py", b"x = 1\n" * 1000),
                (f"{TOP}/src/small.py", b"x = 1\n"),
            ],
        )

        sources = extract_source_archive(archive, tmp_path / "out", Settings(max_file_bytes=100))

        assert set(sources) == {"src/small.py"}

    def test_corrupt_archive_raises_a_workspace_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.tar.gz"
        broken.write_bytes(b"not a tarball at all")

        with pytest.raises(WorkspaceError, match="Could not read"):
            extract_source_archive(broken, tmp_path / "out", Settings())


class TestWorkspaceLifecycle:
    def test_workspace_is_removed_afterwards(self) -> None:
        with temporary_workspace() as root:
            created = root
            (root / "scratch.txt").write_text("temp")
            assert created.exists()

        assert not created.exists()

    def test_workspace_is_removed_even_after_an_error(self) -> None:
        created: Path | None = None
        with pytest.raises(RuntimeError), temporary_workspace() as root:
            created = root
            raise RuntimeError("boom")

        assert created is not None
        assert not created.exists()
