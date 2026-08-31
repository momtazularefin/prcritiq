"""Benchmark dataset construction from real pull request review history.

Labels come from inline review comments humans actually left on real pull
requests. Those comments carry a file path and a line, which is exactly the
shape a finding has, so a label is a real reviewer's judgment rather than
something invented for the benchmark.

The filter below is mechanical and documented, not hand-adjudicated. That is a
real limitation: it approximates the eval plan's labeling rules with heuristics,
so a report built on this dataset must say so rather than implying a human
curated every label.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

#: Comments that carry no reviewable claim. These implement the eval plan's
#: exclusion list: praise, social replies, bikeshedding, and project management.
_SOCIAL: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(lgtm|nit|thanks?|thank you|nice|good catch|ok|okay|done|\+1|👍|同意)\b",
        r"^\s*(ship it|looks good|great work|awesome|perfect)\b",
        r"^\s*@[\w-]+\s*$",
        r"^\s*(can you rebase|please rebase|needs? rebase)\b",
        r"^\s*(typo|nitpick)\s*[:.]?\s*$",
    )
)

#: Formatting and naming preferences, which the eval plan excludes from labels
#: because PRCritiq is not supposed to report them either.
_STYLE: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(black|isort|ruff format|formatting|whitespace|blank line|indentation)\b",
        r"\b(rename this|naming|call it|better name)\b",
        r"\b(alphabetical|sort these|ordering of imports)\b",
    )
)

_MIN_COMMENT_CHARACTERS: Final = 40
_MIN_LABELS_PER_CASE: Final = 1


@dataclass(frozen=True)
class Label:
    """One human review comment kept as an expected issue."""

    file_path: str
    line: int
    category: str
    severity: str
    human_comment: str
    expected_issue: str
    match_notes: str = ""


@dataclass(frozen=True)
class BenchmarkCase:
    """One pull request in the benchmark corpus."""

    id: str
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    languages: list[str]
    diff_path: str
    human_comments_path: str
    labels: list[Label] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["labels"] = [asdict(label) for label in self.labels]
        return payload


def is_meaningful_comment(body: str) -> bool:
    """Apply the eval plan's exclusion rules to one review comment.

    Deliberately conservative: a comment that survives is not guaranteed to name
    a defect, only to be substantive enough that a reviewer raising it counts as
    a signal worth measuring against.
    """

    text = body.strip()
    if len(text) < _MIN_COMMENT_CHARACTERS:
        return False
    if any(pattern.search(text) for pattern in _SOCIAL):
        return False
    return all(not pattern.search(text) for pattern in _STYLE)


def classify_comment(body: str) -> tuple[str, str]:
    """Guess a category and severity from the reviewer's own words.

    A heuristic, recorded as one. It shapes reporting only; matching a finding
    to a label never depends on the category agreeing.
    """

    text = body.lower()
    if re.search(r"\b(security|injection|escape|sanitiz|auth|secret|token leak)\b", text):
        return "security", "high"
    if re.search(r"\b(race|deadlock|leak|crash|None|null|exception|traceback)\b", text):
        return "bug", "high"
    if re.search(r"\b(test|coverage|assert)\b", text):
        return "test_gap", "medium"
    if re.search(r"\b(error handling|raise|except|catch|fail)\b", text):
        return "error_handling", "medium"
    if re.search(r"\b(api|signature|contract|backward compat|breaking)\b", text):
        return "contract", "medium"
    if re.search(r"\b(regress|broke|no longer)\b", text):
        return "regression", "high"
    return "bug", "medium"


def labels_from_comments(comments: Iterable[dict[str, Any]]) -> list[Label]:
    """Turn a pull request's inline review comments into labels."""

    labels: list[Label] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        path = str(comment.get("path") or "")
        line = comment.get("line") or comment.get("original_line")
        if not path or not line or not is_meaningful_comment(body):
            continue
        category, severity = classify_comment(body)
        labels.append(
            Label(
                file_path=path,
                line=int(line),
                category=category,
                severity=severity,
                human_comment=body.strip(),
                expected_issue=body.strip()[:280],
                match_notes="derived from an inline review comment",
            )
        )
    return labels


def write_dataset(cases: Sequence[BenchmarkCase], path: Path) -> int:
    """Write cases as JSON Lines, one case per line."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_json(), sort_keys=True) + "\n")
    return len(cases)


def read_dataset(path: Path) -> list[BenchmarkCase]:
    """Read a dataset written by `write_dataset`."""

    cases: list[BenchmarkCase] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        payload = json.loads(raw)
        labels = [Label(**item) for item in payload.pop("labels", [])]
        cases.append(BenchmarkCase(**payload, labels=labels))
    return cases


def case_is_usable(case: BenchmarkCase) -> bool:
    """A case earns its place only if a human left something to measure against."""

    return len(case.labels) >= _MIN_LABELS_PER_CASE
