"""Tests for unified-diff parsing and changed-line validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from prcritiq.diff import (
    DiffIndex,
    DiffParseError,
    FileDiff,
    build_diff_index,
    build_file_diff,
    detect_language,
    file_diff_from_changed_file,
    parse_patch,
)
from prcritiq.github import ChangedFile

FIXTURES = Path(__file__).parent / "fixtures" / "patches"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.patch").read_text(encoding="utf-8")


def python_diff(
    patch: str | None, *, path: str = "src/app.py", status: str = "modified"
) -> FileDiff:
    return build_file_diff(
        path=path,
        status=status,
        additions=1,
        deletions=0,
        changes=1,
        patch=patch,
    )


class TestParsePatch:
    def test_empty_patch_yields_no_hunks(self) -> None:
        assert parse_patch(None) == ()
        assert parse_patch("") == ()

    def test_multi_hunk_patch_maps_added_lines_in_both_hunks(self) -> None:
        hunks = parse_patch(load("simple_modification"))

        assert len(hunks) == 2
        assert hunks[0].added_new_lines == (3, 4, 5)
        assert hunks[0].removed_old_lines == (3, 4)
        assert hunks[1].added_new_lines == (23,)
        assert hunks[1].section_heading == "def save(path, data):"

    def test_second_hunk_numbering_is_offset_by_its_own_header(self) -> None:
        """A hunk further down the file must not inherit the first hunk cursor."""

        hunks = parse_patch(load("simple_modification"))

        assert hunks[1].new_start == 21
        assert hunks[1].old_start == 20

    def test_new_file_numbers_every_added_line_from_one(self) -> None:
        hunks = parse_patch(load("new_file"))

        assert len(hunks) == 1
        assert hunks[0].old_count == 0
        assert hunks[0].added_new_lines == (1, 2, 3, 4)
        assert hunks[0].removed_old_lines == ()

    def test_deleted_file_has_removals_and_no_additions(self) -> None:
        hunks = parse_patch(load("deleted_file"))

        assert hunks[0].added_new_lines == ()
        assert hunks[0].removed_old_lines == (1, 2, 3)

    def test_bare_empty_line_counts_as_blank_context(self) -> None:
        """GitHub emits an empty string, not a single space, for a blank context line."""

        hunks = parse_patch(load("blank_context_lines"))

        # If the empty line were dropped, the addition would be numbered 2.
        assert hunks[0].added_new_lines == (3,)

    def test_no_newline_marker_consumes_no_line(self) -> None:
        hunks = parse_patch(load("no_newline_at_eof"))

        assert hunks[0].added_new_lines == (2,)
        assert hunks[0].removed_old_lines == (2,)

    def test_rename_preamble_is_tolerated(self) -> None:
        hunks = parse_patch(load("rename_with_edit"))

        assert len(hunks) == 1
        assert hunks[0].added_new_lines == (5,)

    def test_hunk_body_disagreeing_with_its_header_is_rejected(self) -> None:
        """A wrong count would silently shift every later line number."""

        with pytest.raises(DiffParseError, match="disagrees with its header"):
            parse_patch(load("malformed_counts"))

    def test_unrecognized_line_prefix_is_rejected(self) -> None:
        with pytest.raises(DiffParseError, match="Unrecognized diff line prefix"):
            parse_patch("@@ -1,1 +1,1 @@\n?corrupt\n")

    def test_content_before_the_first_hunk_is_rejected(self) -> None:
        with pytest.raises(DiffParseError, match="before the first hunk header"):
            parse_patch("this is not a diff\n@@ -1,1 +1,1 @@\n alpha\n")


class TestLanguageDetection:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/app.py", "python"),
            ("src/app.pyi", "python"),
            ("web/main.TSX", "typescript"),
            ("web/main.mjs", "javascript"),
            ("cmd/main.go", "go"),
            ("README.md", "markdown"),
            ("Makefile", "unknown"),
        ],
    )
    def test_detect_language(self, path: str, expected: str) -> None:
        assert detect_language(path) == expected


class TestChangedFileAdapter:
    def test_adapter_preserves_rename_origin_and_parses_patch(self) -> None:
        changed = ChangedFile(
            filename="src/new_name.py",
            status="renamed",
            additions=1,
            deletions=0,
            changes=1,
            patch=load("rename_with_edit"),
            previous_filename="src/old_name.py",
        )

        result = file_diff_from_changed_file(changed)

        assert result.previous_path == "src/old_name.py"
        assert result.language == "python"
        assert result.changed_new_lines == (5,)


class TestCommentableLines:
    def test_changed_lines_are_deduplicated_and_sorted(self) -> None:
        result = python_diff(load("simple_modification"))

        assert result.changed_new_lines == (3, 4, 5, 23)

    def test_deleted_file_offers_no_commentable_line(self) -> None:
        """A removed file has no head-revision line an inline comment could target."""

        result = python_diff(load("deleted_file"), status="removed")

        assert result.commentable_lines == frozenset()


class TestLineValidation:
    @pytest.fixture
    def index(self) -> DiffIndex:
        return build_diff_index([python_diff(load("simple_modification"))])

    def test_added_line_is_valid(self, index: DiffIndex) -> None:
        verdict = index.validate_line("src/app.py", 4)

        assert verdict.valid is True
        assert verdict.reason == "ok"

    def test_context_line_is_not_commentable(self, index: DiffIndex) -> None:
        """Line 1 is visible in the diff as context, but this PR did not change it."""

        verdict = index.validate_line("src/app.py", 1)

        assert verdict.valid is False
        assert verdict.reason == "line_not_changed"

    def test_unknown_file_is_rejected(self, index: DiffIndex) -> None:
        verdict = index.validate_line("src/untouched.py", 4)

        assert verdict.valid is False
        assert verdict.reason == "unknown_file"

    @pytest.mark.parametrize("line", [0, -1])
    def test_non_positive_line_is_rejected(self, index: DiffIndex, line: int) -> None:
        verdict = index.validate_line("src/app.py", line)

        assert verdict.valid is False
        assert verdict.reason == "non_positive_line"

    def test_partition_keeps_suppressed_targets_for_reporting(self, index: DiffIndex) -> None:
        kept, suppressed = index.partition_line_targets(
            [("src/app.py", 3), ("src/app.py", 1), ("missing.py", 9), ("src/app.py", 23)]
        )

        assert [item.line for item in kept] == [3, 23]
        assert [item.reason for item in suppressed] == ["line_not_changed", "unknown_file"]

    def test_unknown_paths_resolve_to_nothing(self, index: DiffIndex) -> None:
        assert index.commentable_lines("nope.py") == frozenset()
        assert index.get("nope.py") is None
