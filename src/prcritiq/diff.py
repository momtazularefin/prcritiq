"""Unified-diff parsing and changed-line validation for pull request patches.

The parser is deliberately strict. A patch whose hunk headers disagree with the
body is rejected rather than parsed on a best-effort basis, because a silently
mis-mapped line number is exactly the failure this module exists to prevent:
AC8 requires that every inline finding lands on a real changed line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # Keeps the diff plane importable without the HTTP client.
    from .github import ChangedFile

_HUNK_HEADER: Final = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)

# Lines git may emit before the first hunk, or between file sections.
_PREAMBLE_PREFIXES: Final[tuple[str, ...]] = (
    "diff ",
    "index ",
    "--- ",
    "+++ ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
)

_LANGUAGE_BY_SUFFIX: Final[dict[str, str]] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".txt": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".lock": "lockfile",
}

#: Languages PRCritiq reasons about in v1 (ADR-004: Python first, JS/TS when safe).
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"python", "javascript", "typescript"})


class DiffParseError(ValueError):
    """Raised when a patch cannot be parsed into a trustworthy line mapping."""


def detect_language(path: str) -> str:
    """Return a coarse language name for a repository path."""

    suffix = PurePosixPath(path).suffix.lower()
    return _LANGUAGE_BY_SUFFIX.get(suffix, "unknown")


@dataclass(frozen=True)
class Hunk:
    """One @@ section of a unified diff."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section_heading: str
    added_new_lines: tuple[int, ...]
    removed_old_lines: tuple[int, ...]


@dataclass(frozen=True)
class FileDiff:
    """One file as changed by a pull request."""

    path: str
    status: str
    language: str
    additions: int
    deletions: int
    changes: int
    patch: str | None
    previous_path: str | None
    hunks: tuple[Hunk, ...]

    @property
    def changed_new_lines(self) -> tuple[int, ...]:
        """Added line numbers in the head revision, ascending."""

        return tuple(sorted({line for hunk in self.hunks for line in hunk.added_new_lines}))

    @property
    def commentable_lines(self) -> frozenset[int]:
        """Lines an inline comment may target.

        Only added lines qualify. Context lines are visible in the diff, but
        commenting on one means commenting on code this pull request did not
        touch, which NFR1 treats as noise.
        """

        return frozenset(self.changed_new_lines)


def parse_patch(patch: str | None) -> tuple[Hunk, ...]:
    """Parse a unified diff patch into hunks with resolved line numbers."""

    if not patch:
        return ()

    hunks: list[Hunk] = []
    header: str | None = None
    match: re.Match[str] | None = None
    added: list[int] = []
    removed: list[int] = []
    old_cursor = 0
    new_cursor = 0

    def close() -> None:
        if match is None or header is None:
            return
        old_count = int(match.group("old_count") or 1)
        new_count = int(match.group("new_count") or 1)
        old_seen = old_cursor - int(match.group("old_start"))
        new_seen = new_cursor - int(match.group("new_start"))
        if old_seen != old_count or new_seen != new_count:
            raise DiffParseError(
                f"Hunk body disagrees with its header {header!r}: "
                f"consumed {old_seen} old and {new_seen} new lines, "
                f"header declares {old_count} and {new_count}"
            )
        hunks.append(
            Hunk(
                header=header,
                old_start=int(match.group("old_start")),
                old_count=old_count,
                new_start=int(match.group("new_start")),
                new_count=new_count,
                section_heading=match.group("section").strip(),
                added_new_lines=tuple(added),
                removed_old_lines=tuple(removed),
            )
        )

    for raw in patch.splitlines():
        found = _HUNK_HEADER.match(raw)
        if found is not None:
            close()
            header = raw
            match = found
            added = []
            removed = []
            old_cursor = int(found.group("old_start"))
            new_cursor = int(found.group("new_start"))
            continue

        if match is None:
            if raw.startswith(_PREAMBLE_PREFIXES) or raw.strip() == "":
                continue
            raise DiffParseError(f"Unexpected line before the first hunk header: {raw!r}")

        if raw.startswith("\\"):
            # "\ No newline at end of file" annotates the previous line only.
            continue
        if raw == "" or raw.startswith(" "):
            # GitHub emits a bare empty string for a blank context line.
            old_cursor += 1
            new_cursor += 1
        elif raw.startswith("+"):
            added.append(new_cursor)
            new_cursor += 1
        elif raw.startswith("-"):
            removed.append(old_cursor)
            old_cursor += 1
        else:
            raise DiffParseError(f"Unrecognized diff line prefix: {raw!r}")

    close()
    return tuple(hunks)


def build_file_diff(
    *,
    path: str,
    status: str,
    additions: int,
    deletions: int,
    changes: int,
    patch: str | None,
    previous_path: str | None = None,
) -> FileDiff:
    """Build a FileDiff, parsing the patch when one is present."""

    return FileDiff(
        path=path,
        status=status,
        language=detect_language(path),
        additions=additions,
        deletions=deletions,
        changes=changes,
        patch=patch,
        previous_path=previous_path,
        hunks=parse_patch(patch),
    )


def file_diff_from_changed_file(changed: ChangedFile) -> FileDiff:
    """Adapt a GitHub changed-file payload into a parsed FileDiff."""

    return build_file_diff(
        path=changed.filename,
        status=changed.status,
        additions=changed.additions,
        deletions=changed.deletions,
        changes=changed.changes,
        patch=changed.patch,
        previous_path=changed.previous_filename,
    )


@dataclass(frozen=True)
class LineValidation:
    """The verdict on one candidate inline-comment target."""

    path: str
    line: int
    valid: bool
    reason: str


class DiffIndex:
    """Path-keyed view over the parsed diff of one pull request."""

    def __init__(self, files: Iterable[FileDiff]) -> None:
        self._files: tuple[FileDiff, ...] = tuple(files)
        self._by_path: dict[str, FileDiff] = {item.path: item for item in self._files}

    @property
    def files(self) -> tuple[FileDiff, ...]:
        return self._files

    def get(self, path: str) -> FileDiff | None:
        return self._by_path.get(path)

    def commentable_lines(self, path: str) -> frozenset[int]:
        found = self._by_path.get(path)
        return found.commentable_lines if found else frozenset()

    def validate_line(self, path: str, line: int) -> LineValidation:
        """Decide whether an inline comment may target this path and line."""

        if line < 1:
            return LineValidation(path, line, False, "non_positive_line")
        found = self._by_path.get(path)
        if found is None:
            return LineValidation(path, line, False, "unknown_file")
        if line not in found.commentable_lines:
            return LineValidation(path, line, False, "line_not_changed")
        return LineValidation(path, line, True, "ok")

    def partition_line_targets(
        self, targets: Iterable[tuple[str, int]]
    ) -> tuple[tuple[LineValidation, ...], tuple[LineValidation, ...]]:
        """Split candidate targets into kept and suppressed verdicts.

        Suppressed targets are returned rather than dropped so a run record can
        report its invalid-line rate instead of hiding one.
        """

        kept: list[LineValidation] = []
        suppressed: list[LineValidation] = []
        for path, line in targets:
            verdict = self.validate_line(path, line)
            (kept if verdict.valid else suppressed).append(verdict)
        return tuple(kept), tuple(suppressed)


def build_diff_index(files: Iterable[FileDiff]) -> DiffIndex:
    """Build a DiffIndex over parsed file diffs."""

    return DiffIndex(files)
